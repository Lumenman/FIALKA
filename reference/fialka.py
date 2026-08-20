#!/usr/bin/env python3
"""Эталонная модель шифровальной машины Фиалка М-125.

Реализует механизм, восстановленный из M125v5_16eng.exe и сверенный
с Fialka_200.pdf (Reuvers & Simons). Назначение — оракул: медленно, зато
буквально повторяет формулы из документа по механизму, чтобы по нему можно
было сверять быструю реализацию.

Индексация всюду 1-базовая, как в оригинале: контакты 1..30, слоты 1..10.

    python fialka.py --selftest
    python fialka.py -e --key keys/kt16_08_26.txt --in msg.txt
"""
import argparse
import configparser
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)          # data/ и keys/ общие с реализацией на Паскале
MACHINE = os.path.join(ROOT, 'data', 'machine-M125-3.ini')
N = 30                      # контактов на диске
SLOTS = 10                  # дисков в барабане


def norm(x):
    """Привести к диапазону 1..N (в оригинале — два цикла вычитания)."""
    return (x - 1) % N + 1


def invert(table):
    """Обратная таблица. table[1..N] -> out[1..N]; 0 означает «нет связи»."""
    out = [0] * (N + 1)
    for i in range(1, N + 1):
        if table[i]:
            out[table[i]] = i
    return out


# Двух знаков в значении INI не выразить: ';' начинает комментарий, а '='
# не может стоять в ключе — он делит строку. Оба пишутся токеном.
ESCAPES = {'\\s': ';', '\\e': '='}


def unescape(token):
    return ESCAPES.get(token, token)


def _row(text):
    """Строка INI -> 1-базовый список. Пустая строка -> все нули."""
    values = [int(v) for v in text.split()] if text.strip() else [0] * N
    return [0] + values


class Tables:
    """Неизменное «железо»: машина плюс комплект дисков."""

    def __init__(self, machine_ini, wheels_ini, head_ini=None):
        # interpolation=None: в верхнем ряду головки есть знак '%'
        m = configparser.ConfigParser(inline_comment_prefixes=(';',), interpolation=None)
        m.read(machine_ini, encoding='utf-8')
        self.script = m['machine'].get('script', '').strip()
        self.space_contact = int(m['machine']['space_contact'])
        self.probe_even = int(m['machine']['probe_even'])
        self.probe_odd = int(m['machine']['probe_odd'])
        # ЦФ и БК — клавиши, а не знаки: их контакты задаёт проводка машины,
        # а какие буквы они вытеснили, зависит от головки.
        self.shift_up = int(m['machine']['shift_up'])
        self.shift_down = int(m['machine']['shift_down'])
        if len({self.shift_up, self.shift_down, self.space_contact}) != 3:
            raise ValueError('ЦФ, БК и пробел не могут сидеть на одном контакте')

        self.entry = _row(m['entry']['wiring'])
        self.entry_inv = invert(self.entry)
        self.card_builtin = _row(m['card']['builtin'])
        self.keyboard = {int(k.split('.')[1]): _row(m[k]['wiring'])
                         for k in m.sections() if k.startswith('keyboard.')}
        self.reflector = {}
        for k in (s for s in m.sections() if s.startswith('reflector.')):
            live = int(k.split('.')[1])
            self.reflector[live] = {
                'coding': _row(m[k]['coding']),
                'decoding': _row(m[k]['decoding']),
                'alt_coding': _row(m[k]['alt_coding']),
                'alt_decoding': _row(m[k]['alt_decoding']),
            }

        # Головка называется строкой script машины; -H подставляет любую.
        self.load_head(head_ini or os.path.join(os.path.dirname(machine_ini),
                                                'head-%s.ini' % self.script),
                       check_id=head_ini is None)

        w = configparser.ConfigParser(inline_comment_prefixes=(';',), interpolation=None)
        w.read(wheels_ini, encoding='utf-8')
        self.wheel_set = w['set']['id'].strip()
        self.wiring, self.wiring_inv, self.pins = [None], [None], [None]
        for i in range(1, SLOTS + 1):
            wiring = _row(w['wheel%d' % i]['wiring'])
            if sorted(wiring[1:]) != list(range(1, N + 1)):
                raise ValueError('диск %d: проводка не перестановка 1..%d' % (i, N))
            pins = {int(v) for v in w['wheel%d' % i]['pins'].split()}
            if pins - set(range(1, N + 1)):
                raise ValueError('диск %d: штифт вне диапазона' % i)
            self.wiring.append(wiring)
            self.wiring_inv.append(invert(wiring))
            self.pins.append(pins)

    # ---- печатающая головка -------------------------------------------------
    def load_head(self, path, check_id=False):
        """Два ряда знаков на тридцати контактах.

        Шифра головка не касается: диски и рефлектор считают контакты, а знаки
        живут только на клавиатуре и на печати. Поэтому её можно менять, не
        трогая машину.
        """
        h = configparser.ConfigParser(inline_comment_prefixes=(';',), interpolation=None)
        h.read(path, encoding='utf-8')
        if 'head' not in h:
            raise ValueError('нет файла головки: %s' % path)
        self.head_id = h['head']['id'].strip()
        # переименованный файл не должен молча оказаться другой головкой
        if check_id and self.head_id != self.script:
            raise ValueError('головка %s лежит в файле с id %s'
                             % (self.script, self.head_id))

        # Оба ряда читаются одинаково: токен на контакт, разделитель — пробел.
        # Знак может быть и многобайтным, и составным (буква плюс диакритика).
        self.alphabet = [unescape(t) for t in h['head']['lower'].split()]
        if len(self.alphabet) != N:
            raise ValueError('в нижнем ряду головки %d знаков, а нужно %d'
                             % (len(self.alphabet), N))
        for i, g in enumerate(self.alphabet, 1):
            if g == '~':
                raise ValueError('контакт %d нижнего ряда пуст, а пустым он быть не '
                                 'может: шифртекст печатается буквами этого ряда' % i)
            if g in self.alphabet[i:]:
                raise ValueError('знак %s в нижнем ряду головки дважды' % g)
        self.upper = [''] + ['' if t == '~' else unescape(t)
                             for t in h['head']['upper'].split()]
        if len(self.upper) != N + 1:
            raise ValueError('в верхнем ряду головки %d знаков, а нужно %d'
                             % (len(self.upper) - 1, N))
        for i, g in enumerate(self.upper[1:], 1):
            if g and g in self.upper[i + 1:]:
                raise ValueError('знак %s в верхнем ряду головки дважды' % g)
        self.upper_index = {g: i for i, g in enumerate(self.upper)
                            if i and g and i not in (self.shift_up, self.shift_down)}

        # Клавиши ЦФ, БК и пробела заняты служебным, и стоявшие на них буквы
        # обязаны найтись в верхнем ряду — иначе набрать их будет нечем
        # (мануал 3.2.10). Знак сразу в двух рядах ошибкой не считается: до
        # верхнего ряда очередь не дойдёт, зато на латинских головках так и
        # сделано — цифра 8 стоит и в нижнем ряду, и в верхнем.
        served = (self.space_contact, self.shift_up, self.shift_down)
        for i, g in enumerate(self.alphabet, 1):
            if i in served and g not in self.upper_index:
                raise ValueError('буква %s стоит на служебном контакте %d, '
                                 'а в верхнем ряду её нет' % (g, i))

        # Режим цифр отпирает десять клавиш, и какие именно — проводка машины
        # (keyboard.10 и парная ей alt_decoding), а не головка. Совпадут ли они
        # с цифрами верхнего ряда — свойство пары «машина плюс головка»: на
        # кириллической совпадают, на латинских нет. Поэтому здесь только
        # считается, а отказ — при входе в режим цифр.
        self.digits = {g: i for g, i in self.upper_index.items() if g.isdigit()}
        if len(self.digits) != 10:
            raise ValueError('в верхнем ряду должно быть десять цифр')
        # по контактам, а не по цифрам: так обе реализации говорят одно и то же
        self.dead_digits = [g for g, i in sorted(self.digits.items(),
                                                 key=lambda kv: kv[1])
                            if not self.keyboard[10][i]]

    def index(self, ch, decoding, num=0):
        """Знак -> номер контакта. Пробел делит контакт с последней буквой."""
        if ch == ' ' and not decoding:
            return self.space_contact
        if ch not in self.alphabet:
            raise ValueError('знак %d (%s): такого знака на машине нет, готовьте '
                             'текст ключом --prepare или смените режим на --mode M'
                             % (num, ch))
        contact = self.alphabet.index(ch) + 1
        # Й делит контакт с пробелом и в открытом тексте молча ушла бы в пробел;
        # в шифртексте контакт 30 печатается как Й, там она законна
        if contact == self.space_contact and not decoding:
            raise ValueError('знак %d (%s): её клавиша занята пробелом, замените '
                             'на И (мануал 4.5) или примените --prepare' % (num, ch))
        return contact

    def letter(self, contact, decoding):
        """Номер контакта -> знак. В расшифровании контакт пробела печатается пробелом."""
        if contact == self.space_contact and decoding:
            return ' '
        return self.alphabet[contact - 1]

    # ---- ряды печатающей головки -------------------------------------------
    def key_of(self, ch, num=0):
        """Знак -> (ряд, контакт) для смешанного режима.

        Нижний ряд — тридцать букв, но клавиши Ф и Ж отданы под ЦФ и БК, а Й
        делит контакт с пробелом. Все три буквы переехали в верхний ряд, к
        соседним клавишам, и набираются оттуда (мануал 3.2.10 и рис. 3.4.8).
        """
        if ch == ' ':
            return 'lower', self.space_contact
        if ch in self.alphabet:
            contact = self.alphabet.index(ch) + 1
            if contact not in (self.shift_up, self.shift_down, self.space_contact):
                return 'lower', contact
        if ch in self.upper_index:
            return 'upper', self.upper_index[ch]
        raise ValueError('знак %d (%s): такого знака нет ни в одном ряду головки, '
                         'готовьте текст ключом --prepare' % (num, ch))

    def typable(self, ch, text_mode):
        """Набирается ли знак в этом режиме текста без подготовки."""
        if ch in ' \r\n':
            return True
        if text_mode == 'N':
            return ch in self.digits
        if text_mode == 'L':
            return ch in self.alphabet and self.alphabet.index(ch) + 1 != self.space_contact
        try:
            self.key_of(ch)
        except ValueError:
            return False
        return True


CARD_ROWS = ('wheel_order', 'ring', 'core_order', 'core_side',
             'core_offset', 'position')


def parse_card(lines):
    """Ключ дня в формате перфокарты (см. мануал, гл. 2.11.1).

    Строка с '=' — заголовок (set, card), строка без — очередной ряд карты.
    Рядов пять, как на настоящей карте; шестой — прорезь — необязателен:
    его отсутствие означает установку по ряду 1, ровно как при загрузке
    карты в оригинальной программе (FUN_00469aec, режим 0).
    """
    head, rows = {}, []
    for line in lines:
        line = line.split(';')[0].strip()
        if not line:
            continue
        if '=' in line:
            name, _, value = line.partition('=')
            head[name.strip()] = value.strip()
        else:
            rows.append(line.split())
    unknown = sorted(set(head) - {'set', 'card'})
    if unknown:
        raise ValueError('неизвестный заголовок: %s' % ', '.join(unknown))
    if len(rows) not in (5, 6):
        raise ValueError('на карте 5 рядов (6-й, прорезь, необязателен), получено %d'
                         % len(rows))
    if len(rows) == 5:
        rows.append(list(rows[0]))
    return head, rows


def read_card(path):
    with open(path, encoding='utf-8') as f:
        return parse_card(f)


class Key:
    """Ключ дня (карта) плюс начальная установка сообщения.

    Режим работы, режим текста и прорезь сюда приходят снаружи: на машине это
    тумблеры оператора и ключ сообщения из отдельной книги, а не карта.

    Режим текста — рычаг Б/С/Ц (L/M/N). Число живых контактов задаёт не он, а
    NumLock, у которого всего два положения: 10 в режиме цифр, 30 во всех
    остальных (мануал 3.2.9). Шифр в буквенном и смешанном одинаков.
    """

    def __init__(self, head, rows, tables, mode='coding', text_mode='L', position=None):
        self.mode, self.text_mode = mode, text_mode
        self.live = 10 if text_mode == 'N' else 30
        if text_mode == 'N' and tables.dead_digits:
            raise ValueError('головка %s: в режиме цифр клавиши цифр %s мертвы — '
                             'какие десять клавиш отпираются, задаёт проводка '
                             'машины, а не головка'
                             % (tables.head_id, ' '.join(tables.dead_digits)))
        for name, row in zip(CARD_ROWS, rows):
            setattr(self, name, self._ten(row, tables))
        if position is not None:
            self.position = self._ten(position.split(), tables)
        self._check()
        card = head.get('card', 'identity')
        if card == 'identity':
            self.card = [0] + list(range(1, N + 1))
        elif card == 'builtin':
            self.card = list(tables.card_builtin)
        else:
            self.card = _row(card)

    @staticmethod
    def _ten(tokens, tables):
        out = []
        for token in tokens:
            if token.isdigit():
                out.append(int(token))
            elif token in tables.alphabet:
                out.append(tables.alphabet.index(token) + 1)
            else:
                raise ValueError('не буква алфавита и не число: %r' % token)
        if len(out) != SLOTS:
            raise ValueError('ожидалось %d значений, получено %d' % (SLOTS, len(out)))
        return [0] + out

    def _check(self):
        """Перепутанный или сдвинутый ряд даёт правдоподобный, но неверный
        шифртекст и ничем себя не выдаёт, поэтому проверяется каждый."""
        for name in ('wheel_order', 'core_order'):
            if sorted(getattr(self, name)[1:]) != list(range(1, SLOTS + 1)):
                raise ValueError('%s: ожидалась перестановка 1..%d' % (name, SLOTS))
        if set(self.core_side[1:]) - {1, 2}:
            raise ValueError('core_side: только 1 или 2')
        for name in ('ring', 'core_offset', 'position'):
            if not all(1 <= v <= N for v in getattr(self, name)[1:]):
                raise ValueError('%s: значение вне 1..%d' % (name, N))


PREPARE = os.path.join(ROOT, 'data', 'prepare.ini')


def _abbreviate(text, table, report):
    """Сокращения: телеграфный стиль до машины.

    В отличие от замен применяются ко всему тексту, а не только к тому, что
    не набирается: МИНУТ набирается прекрасно, сокращают его не поэтому. При
    пустой таблице текст не меняется вовсе.

    На каждом месте берётся самое длинное подошедшее сокращение — иначе
    порядок строк в файле решал бы, во что превратится текст.
    """
    if not table:
        return text
    out, i = [], 0
    while i < len(text):
        hit = ''
        for key in table:
            if len(key) > len(hit) and text.startswith(key, i):
                hit = key
        if not hit:
            out.append(text[i])
            i += 1
            continue
        if report:
            report('подготовка: сокращение %s -> %s' % (hit, table[hit]))
        out.append(table[hit])
        i += len(hit)
    return ''.join(out)


def prepare(text, tables, path=PREPARE, report=None, text_mode='L'):
    """Подготовка текста: то, что оператор делал до машины.

    Сначала сокращения, потом замены — сокращение может привести знак, который
    придётся заменять. Заменяется только то, что в этом режиме набрать нельзя:
    в смешанном цифры и пунктуация идут на машину как есть, а в буквенном их
    приходится писать словами. Регистр приводится к прописным всегда и до
    обеих таблиц. Всё документируется: молча менять сообщение нельзя —
    получатель расшифрует не то, что отправляли. Номера знаков в отказах
    считаются по тексту после сокращений: до машины доезжает он.
    """
    # delimiters: у FPC разделитель только '=', и двоеточие обязано быть
    # обычным ключом. optionxform: иначе ключи-буквы уедут в нижний регистр.
    cfg = configparser.ConfigParser(delimiters=('=',), inline_comment_prefixes=(';',))
    cfg.optionxform = str
    if not cfg.read(path, encoding='utf-8'):
        raise ValueError('нет таблицы подготовки: %s' % path)
    # escape раскрывается до приведения регистра: иначе '\\s' стало бы '\\S'
    table = {unescape(k).upper(): unescape(v).upper()
             for k, v in cfg['replace'].items()}
    short = ({unescape(k).upper(): unescape(v).upper()
              for k, v in cfg['abbreviate'].items()}
             if cfg.has_section('abbreviate') else {})
    text = _abbreviate(text.upper(), short, report)
    out, num = [], 0
    for ch in text:
        if ch not in '\r\n':
            num += 1
        if tables.typable(ch, text_mode):
            out.append(ch)
        elif ch in table:
            if report:
                report('подготовка: знак %d  %s -> %s' % (num, ch, table[ch]))
            out.append(table[ch])
        else:
            raise ValueError('знак %d (%s): в этом режиме не набирается, и замены '
                             'для него в таблице подготовки нет' % (num, ch))
    return ''.join(out)


def load(machine_path, key_path, wheels_path=None, head_path=None, **kw):
    """Собрать таблицы и ключ. Комплект берётся из ключа, если не задан явно."""
    head, rows = read_card(key_path)
    if wheels_path is None:
        if 'set' not in head:
            raise ValueError('в ключе нет "set = ", а комплект не задан явно')
        wheels_path = os.path.join(os.path.dirname(machine_path),
                                   'wheels-%s.ini' % head['set'])
        tables = Tables(machine_path, wheels_path, head_path)
        # переименованный файл не должен молча оказаться другим комплектом
        if tables.wheel_set != head['set']:
            raise ValueError('комплект %s лежит в файле с id %s'
                             % (head['set'], tables.wheel_set))
    else:
        tables = Tables(machine_path, wheels_path, head_path)
    return tables, Key(head, rows, tables, **kw)


class Machine:
    """Собственно машина. Формулы — один в один из документа по механизму."""

    def __init__(self, tables, key):
        self.t = tables
        self.k = key
        self.pos = list(key.position)
        self.card = key.card
        self.card_inv = invert(self.card)
        self.kb = tables.keyboard[key.live]
        self.kb_inv = invert(self.kb)
        refl = tables.reflector[key.live]
        if key.mode == 'plain':
            ident = [0] + list(range(1, N + 1))
            self.refl, self.alt = ident, [0] * (N + 1)
        elif key.mode == 'decoding':
            self.refl, self.alt = refl['decoding'], refl['alt_decoding']
        else:
            self.refl, self.alt = refl['coding'], refl['alt_coding']
        # «мёртвые» контакты рефлектора возвращают сигнал в барабан
        self.reentry_in = refl['alt_coding'] if key.mode != 'plain' else [0] * (N + 1)
        self.reentry_out = refl['alt_decoding'] if key.mode != 'plain' else [0] * (N + 1)

    # ---- проход через один диск -------------------------------------------
    def hop(self, slot, x, back=False):
        pos = self.pos[slot] + self.k.ring[slot]
        off = self.k.core_offset[slot]
        side = self.k.core_side[slot]
        core = self.k.core_order[slot]

        x = x + pos + 1
        x = (N + 1 + off - x) if side == 1 else (x - off + 1)
        x = norm(x)
        if side == 1:
            table = self.t.wiring[core] if back else self.t.wiring_inv[core]
            x = (N + 2) - table[x] + off
        else:
            table = self.t.wiring_inv[core] if back else self.t.wiring[core]
            x = table[x] + off
        return norm(x - pos - 2)

    # ---- штифт под щупом ---------------------------------------------------
    def pin(self, slot):
        probe = self.t.probe_even if slot % 2 == 0 else self.t.probe_odd
        p = norm(self.pos[slot] + self.k.ring[slot] + probe)
        return p in self.t.pins[self.k.wheel_order[slot]]

    # ---- один такт шагового механизма -------------------------------------
    def step(self):
        p = {s: self.pin(s) for s in (9, 7, 5, 3, 2, 4, 6, 8)}
        forward, backward, blocked = [9], [2], False
        for s in (7, 5, 3, 1):
            blocked = blocked or p[s + 2]
            if not blocked:
                forward.append(s)
        blocked = False
        for s in (4, 6, 8, 10):
            blocked = blocked or p[s - 2]
            if not blocked:
                backward.append(s)
        for s in forward:
            self.pos[s] = 1 if self.pos[s] >= N else self.pos[s] + 1
        for s in backward:
            self.pos[s] = N if self.pos[s] <= 1 else self.pos[s] - 1

    # ---- один знак ---------------------------------------------------------
    def contact(self, contact_in):
        i = self.kb[contact_in]
        if not i:      # при live 26 и 10 часть клавиш отключена
            raise ValueError('контакт %d не набирается при %d живых контактах'
                             % (contact_in, self.k.live))
        for _ in range(N):                       # возвратный контур на входе
            u = self.t.entry[self.card[i]]
            for slot in range(SLOTS, 0, -1):
                u = self.hop(slot, u)
            if self.refl[u]:
                i = self.refl[u]
                break
            i = self.reentry_in[u]
        else:
            raise RuntimeError('вход: сигнал не вышел из контура')
        for _ in range(N):                       # возвратный контур на выходе
            u = i
            for slot in range(1, SLOTS + 1):
                u = self.hop(slot, u, back=True)
            t = self.card_inv[self.t.entry_inv[u]]
            if self.kb_inv[t]:
                return self.kb_inv[t]
            i = self.reentry_out[t]
        raise RuntimeError('выход: сигнал не вышел из контура')

    def _run(self, contacts):
        """Прогнать готовую последовательность контактов: шифр плюс шаг."""
        out = []
        for c in contacts:
            out.append(self.contact(c))
            self.step()
        return out

    def process(self, text):
        decoding = self.k.mode == 'decoding'
        if self.k.text_mode == 'N':
            return self._numbers(text)
        if self.k.text_mode == 'M':
            return self._mixed_out(text) if decoding else self._mixed_in(text)
        out, num = [], 0
        for ch in text:
            if ch in '\r\n':
                continue
            num += 1
            c = self.contact(self.t.index(ch, decoding, num))
            out.append(self.t.letter(c, decoding))
            self.step()
        return ''.join(out)

    # ---- режим цифр: и вход, и выход цифрами -------------------------------
    def _numbers(self, text):
        """Живы десять клавиш, печатается верхний ряд головки. Пробела нет:
        контакт 30 в этом положении мёртв, поэтому пробелы во входе — просто
        разбивка на группы и пропускаются."""
        contacts, num = [], 0
        for ch in text:
            if ch in ' \r\n':
                continue
            num += 1
            if ch not in self.t.digits:
                raise ValueError('знак %d (%s): в режиме цифр набираются только '
                                 'цифры' % (num, ch))
            contacts.append(self.t.digits[ch])
        return ''.join(self.t.upper[c] for c in self._run(contacts))

    # ---- смешанный: регистр раскрывается на входе --------------------------
    def _mixed_in(self, text):
        """ЦФ и БК оператор жал сам; программа расставляет их по знакам текста.
        В конце головка возвращается к буквам, чтобы следующее сообщение
        начиналось с известного регистра."""
        contacts, register, num = [], 'lower', 0
        for ch in text:
            if ch in '\r\n':
                continue
            num += 1
            row, contact = self.t.key_of(ch, num)
            if row != register:
                contacts.append(self.t.shift_up if row == 'upper' else self.t.shift_down)
                register = row
            contacts.append(contact)
        if register == 'upper':
            contacts.append(self.t.shift_down)
        return ''.join(self.t.letter(c, False) for c in self._run(contacts))

    # ---- смешанный: регистр собирается на выходе ---------------------------
    def _mixed_out(self, text):
        """Переключения печати не дают — они и есть команда печатающей головке."""
        contacts, num = [], 0
        for ch in text:
            if ch in '\r\n':
                continue
            num += 1
            contacts.append(self.t.index(ch, True, num))
        out, register = [], 'lower'
        for c in self._run(contacts):
            if c == self.t.shift_up:
                register = 'upper'
            elif c == self.t.shift_down:
                register = 'lower'
            elif register == 'upper' and self.t.upper[c]:
                out.append(self.t.upper[c])
            else:
                # контакт пробела верхнего знака не имеет: печатается пробелом
                out.append(self.t.letter(c, True))
        return ''.join(out)


# ---------------------------------------------------------------------------
def _fmt(values, alphabet=None):
    """Ряд карты: две группы по пять, как в книгах ключей.

    Головку, у которой в нижнем ряду есть цифры (латинские все такие: 26 букв
    на тридцать контактов, четыре лишних отданы цифрам), буквами записать
    нельзя — '5' на карте не отличить от числа 5. Такой ключ печатается
    числами.
    """
    if alphabet and any(g.isdigit() for g in alphabet):
        alphabet = None
    out = [alphabet[v - 1] if alphabet else str(v) for v in values]
    return '%-9s   %s' % (' '.join(out[:5]), ' '.join(out[5:]))


def gen_positions(alphabet):
    """Ключ сообщения: начальная установка десяти дисков (мануал, гл. 2.11.2)."""
    return _fmt([random.randint(1, N) for _ in range(SLOTS)], alphabet)


def gen_card(tables, wheel_set, card='identity', position=False):
    """Случайный ключ дня в формате карты.

    Прорезь печатается только для самопроверок: на настоящей карте её нет,
    начальная установка берётся из книги ключей сообщения.
    """
    a = tables.alphabet
    rnd = lambda: [random.randint(1, N) for _ in range(SLOTS)]
    lines = ['; ключ дня, комплект %s' % wheel_set,
             'set  = %s' % wheel_set,
             'card = %s' % card,
             '',
             _fmt(random.sample(range(1, SLOTS + 1), SLOTS), a) + '   ; 1 обода по слотам',
             _fmt(rnd(), a) + '   ; 2 кольца',
             _fmt(random.sample(range(1, SLOTS + 1), SLOTS), a) + '   ; 3 сердечники по слотам',
             _fmt([random.choice((1, 2)) for _ in range(SLOTS)]) + '   ; 4 сторона сердечника',
             _fmt(rnd(), a) + '   ; 5 смещение сердечника']
    if position:
        lines.append(_fmt(rnd(), a) + '   ; 6 прорезь')
    return '\n'.join(lines) + '\n'


def _random_key(tables, mode='coding', text_mode='L'):
    """Случайный ключ через генератор и разбор — так самопроверки заодно
    гоняют формат карты, а не только механизм."""
    text = gen_card(tables, tables.wheel_set, card='builtin', position=True)
    head, rows = parse_card(text.splitlines())
    return Key(head, rows, tables, mode=mode, text_mode=text_mode)


def check_card(tables):
    """Разбор карты: молчаливо принятый кривой ключ опаснее падения."""
    five = gen_card(tables, tables.wheel_set)
    head, rows = parse_card(five.splitlines())
    assert head == {'set': tables.wheel_set, 'card': 'identity'}, head
    assert len(rows) == 6 and rows[5] == rows[0], 'нет прорези -> должна равняться ряду 1'
    six = parse_card(gen_card(tables, tables.wheel_set, position=True).splitlines())[1]
    assert len(six) == 6, 'шестой ряд должен читаться как есть'
    bad = [
        (five.replace('set  =', 'sett ='), 'неизвестный заголовок'),
        ('\n'.join(five.splitlines()[:-1]), 'мало рядов'),
        (five + 'А Б В Г Д   Е Ж З И К\nА Б В Г Д   Е Ж З И К\n', 'много рядов'),
    ]
    for text, what in bad:
        try:
            parse_card(text.splitlines())
        except ValueError:
            continue
        raise AssertionError('принят кривой ключ: ' + what)
    for row, what in ((0, 'обода не перестановка'), (3, 'сторона не 1/2')):
        lines = five.splitlines()
        lines[4 + row] = 'А А В Г Д   Е Ж З И К'
        try:
            Key(*parse_card(lines), tables)
        except ValueError:
            continue
        raise AssertionError('принят кривой ключ: ' + what)


def selftest(tables, rounds=200):
    """Три проверки, не требующие эталонных данных.

    1. В режиме plain рефлектор тождественный, значит F = A^-1 * I * A = I:
       машина обязана быть сквозной. Ловит любую ошибку в прямом/обратном
       проходе, входном диске, карте и их инверсиях.
    2. Шифрование и расшифрование при одинаковом старте обязаны быть взаимно
       обратны: coding и decoding — взаимно обратные таблицы рефлектора.
    3. Разбор ключа-карты, включая отказ от кривого ключа.
    """
    random.seed(20260817)
    check_card(tables)
    for _ in range(rounds):
        key = _random_key(tables, 'plain')
        m = Machine(tables, key)
        for c in range(1, N + 1):
            got = m.contact(c)
            if got != c:
                return 'plain не сквозной: %d -> %d' % (c, got)
    # Й набрать нельзя — её клавиша занята пробелом, поэтому в открытом
    # тексте она заранее заменяется на И (см. мануал, гл. 4.5).
    typable = [g for i, g in enumerate(tables.alphabet, 1)
               if i != tables.space_contact] + [' ']
    for _ in range(rounds // 4):
        key = _random_key(tables, 'coding')
        msg = ''.join(random.choice(typable) for _ in range(40))
        enc = Machine(tables, key).process(msg)
        key.mode = 'decoding'
        dec = Machine(tables, key).process(enc)
        if dec != msg:
            return 'обратимость нарушена:\n  %s\n  %s' % (msg, dec)

    # смешанный и цифровой: обратимость вместе с автоматикой регистра
    mixed = sorted(tables.upper_index) + [c for c in typable
                                          if tables.typable(c, 'M')]
    modes = [('M', mixed)]
    if not tables.dead_digits:      # латинские головки в режим цифр не умеют
        modes.append(('N', sorted(tables.digits)))
    for text_mode, pool in modes:
        for _ in range(rounds // 8):
            key = _random_key(tables, 'coding', text_mode)
            msg = ''.join(random.choice(pool) for _ in range(40))
            enc = Machine(tables, key).process(msg)
            key.mode = 'decoding'
            dec = Machine(tables, key).process(enc)
            if dec != msg:
                return ('обратимость нарушена в режиме %s:\n  %s\n  %s'
                        % (text_mode, msg, dec))
    return None


def verify_demo():
    """Сверка с demo/: de.txt -> en.txt на ключе 16.08.26, комплект 6K.

    Регрессионный вектор: шифртекст в demo/en.txt построен этой реализацией,
    поэтому проверка стережёт неизменность поведения, но не доказывает
    совпадение с оригиналом. См. demo/note.txt.
    """
    paths = {
        'machine': MACHINE,
        'key': os.path.join(ROOT, 'keys', 'kt16_08_26.txt'),
        'plain': os.path.join(ROOT, 'demo', 'de.txt'),
        'cipher': os.path.join(ROOT, 'demo', 'en.txt'),
    }
    missing = [n for n, p in paths.items() if not os.path.exists(p)]
    if missing:
        return 'пропущена (нет: %s)' % ', '.join(missing)
    # комплект 6K подтягивается по "set =" из самого ключа
    tables, key = load(paths['machine'], paths['key'])
    plain = open(paths['plain'], encoding='utf-8').read().strip()
    expect = open(paths['cipher'], encoding='utf-8').read().strip()
    got = Machine(tables, key).process(plain)
    if got != expect:
        return 'РАСХОЖДЕНИЕ\n  получено: %s\n  ожидалось: %s' % (got, expect)
    return None


TEXT_MODES = {'L': 'L', 'M': 'M', 'N': 'N', 'Б': 'L', 'С': 'M', 'Ц': 'N'}


def text_mode_arg(value):
    """Режим текста: латиницей или русскими буквами с рычага."""
    key = value.upper()
    if key not in TEXT_MODES:
        raise argparse.ArgumentTypeError('режим текста: L, M или N (Б, С, Ц)')
    return TEXT_MODES[key]


VECTORS = os.path.join(ROOT, 'demo', 'vectors.txt')


def verify_vectors(path=VECTORS):
    """Векторы, снятые с оригинальной программы: три режима текста.

    В отличие от demo/de.txt эти пары построены не нами, поэтому они
    доказывают совпадение с оригиналом, а не только неизменность поведения.
    """
    if not os.path.exists(path):
        return 'пропущена (нет %s)' % path
    key_path = os.path.join(ROOT, 'keys', 'kt16_08_26.txt')
    if not os.path.exists(key_path):
        return 'пропущена (нет ключа)'
    bad, count = [], 0
    for line in open(path, encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith(';'):
            continue
        text_mode, plain, cipher = (part.strip() for part in line.split('|'))
        count += 1
        tables, key = load(MACHINE, key_path, text_mode=text_mode)
        got = Machine(tables, key).process(plain)
        if got != cipher:
            bad.append('%s: %s -> %s, ожидалось %s' % (text_mode, plain, got, cipher))
        tables, key = load(MACHINE, key_path, mode='decoding', text_mode=text_mode)
        back = Machine(tables, key).process(cipher)
        if back != plain:
            bad.append('%s: %s -> %s, ожидалось %s' % (text_mode, cipher, back, plain))
    if bad:
        return 'РАСХОЖДЕНИЕ\n  ' + '\n  '.join(bad)
    return 'OK — векторов %d, оба направления' % count


def main():
    ap = argparse.ArgumentParser(description='Эталонная модель Фиалки М-125')
    ap.add_argument('--machine', default=MACHINE)
    ap.add_argument('--wheels', help='перебить комплект, заданный в ключе')
    ap.add_argument('--head', help='перебить головку, заданную строкой script')
    ap.add_argument('--key', help='ключ дня (карта)')
    ap.add_argument('--pos', help='ключ сообщения: 10 букв начальной установки')
    ap.add_argument('--mode', dest='text_mode', default='L', type=text_mode_arg,
                    help='режим текста, рычаг Б/С/Ц: L буквы (по умолчанию), '
                         'M смешанный, N цифры')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('-e', dest='mode', action='store_const', const='coding',
                      help='зашифрование (по умолчанию)')
    mode.add_argument('-d', dest='mode', action='store_const', const='decoding',
                      help='расшифрование')
    mode.add_argument('-p', dest='mode', action='store_const', const='plain',
                      help='сквозной прогон, для проверки')
    ap.set_defaults(mode='coding')
    ap.add_argument('--prepare', action='store_true',
                    help='подготовить текст по data/prepare.ini, с отчётом')
    ap.add_argument('--in', dest='src', help='входной файл (иначе stdin)')
    ap.add_argument('--selftest', action='store_true', help='структурные проверки')
    ap.add_argument('--genkey', action='store_true', help='случайный ключ дня')
    ap.add_argument('--genpos', action='store_true', help='случайный ключ сообщения')
    ap.add_argument('--set', default='6K', help='комплект для --genkey')
    ap.add_argument('--seed', type=int, help='зерно ГПСЧ, для воспроизводимости')
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    if args.genkey or args.genpos:
        wheels = args.wheels or os.path.join(os.path.dirname(args.machine),
                                             'wheels-%s.ini' % args.set)
        tables = Tables(args.machine, wheels, args.head)
        sys.stdout.write(gen_positions(tables.alphabet) + '\n' if args.genpos
                         else gen_card(tables, tables.wheel_set))
        return 0

    if args.selftest:
        wheels = args.wheels or os.path.join(os.path.dirname(args.machine),
                                             'wheels-6K.ini')
        tables = Tables(args.machine, wheels, args.head)
        print('комплект дисков: %s, головка: %s' % (tables.wheel_set, tables.head_id))
        structural = selftest(tables)
        print('структурные проверки:', 'ПРОВАЛ — ' + structural if structural else 'OK')
        demo = verify_demo()
        print('сверка с demo/:', demo if demo else 'OK — знак в знак')
        vectors = verify_vectors()
        print('векторы с оригинала:', vectors)
        # сменная головка: тот же шифр, другие знаки на контактах
        latin = os.path.join(os.path.dirname(args.machine), 'head-poland.ini')
        if not os.path.exists(latin):
            head = 'пропущена (нет head-poland.ini)'
        else:
            other = Tables(args.machine, wheels, latin)
            head = selftest(other)
            head = 'ПРОВАЛ — ' + head if head else 'OK — головка %s, оба ряда' % other.head_id
        print('сменная головка:', head)
        return 1 if (structural or (demo and 'пропущена' not in demo)
                     or vectors.startswith('РАСХОЖДЕНИЕ')
                     or head.startswith('ПРОВАЛ')) else 0

    if not args.key:
        ap.error('нужен --key, --genkey, --genpos или --selftest')
    if args.prepare and args.mode == 'decoding':
        # на шифртексте подготовка подменила бы Й и испортила его
        ap.error('--prepare применяется только к открытому тексту')
    tables, key = load(args.machine, args.key, args.wheels, args.head,
                       mode=args.mode, text_mode=args.text_mode, position=args.pos)
    text = open(args.src, encoding='utf-8').read() if args.src else sys.stdin.read()
    # без .strip(): пробел — знак машины, переводы строк process пропускает сам
    text = text.upper()          # регистра на машине нет: «п» и «П» — одна клавиша
    if args.prepare:
        text = prepare(text, tables, report=lambda s: print(s, file=sys.stderr),
                       text_mode=args.text_mode)
    sys.stdout.write(Machine(tables, key).process(text) + '\n')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as err:     # кривой ключ — не повод для трассы
        sys.exit('ошибка: %s' % err)
