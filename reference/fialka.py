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


def _row(text):
    """Строка INI -> 1-базовый список. Пустая строка -> все нули."""
    values = [int(v) for v in text.split()] if text.strip() else [0] * N
    return [0] + values


class Tables:
    """Неизменное «железо»: машина плюс комплект дисков."""

    def __init__(self, machine_ini, wheels_ini):
        m = configparser.ConfigParser(inline_comment_prefixes=(';',))
        m.read(machine_ini, encoding='utf-8')
        self.alphabet = m['machine']['alphabet'].strip()
        self.space_contact = int(m['machine']['space_contact'])
        self.probe_even = int(m['machine']['probe_even'])
        self.probe_odd = int(m['machine']['probe_odd'])
        if len(self.alphabet) != N or len(set(self.alphabet)) != N:
            raise ValueError('алфавит должен содержать %d различных знаков' % N)

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

        w = configparser.ConfigParser(inline_comment_prefixes=(';',))
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

    def index(self, ch, decoding, num=0):
        """Знак -> номер контакта. Пробел делит контакт с последней буквой."""
        if ch == ' ' and not decoding:
            return self.space_contact
        if ch not in self.alphabet:
            raise ValueError('знак %d (%s): такого знака на машине нет, '
                             'готовьте текст ключом --prepare' % (num, ch))
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

    Режим, живые контакты и прорезь сюда приходят снаружи: на машине это
    тумблеры оператора и ключ сообщения из отдельной книги, а не карта.
    """

    def __init__(self, head, rows, tables, mode='coding', live=30, position=None):
        self.mode, self.live = mode, live
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


def prepare(text, tables, path=PREPARE, report=None):
    """Подготовка текста: то, что оператор делал до машины.

    Регистр приводится к прописным всегда и до таблицы. Замены документируются:
    молча менять сообщение нельзя — получатель расшифрует не то, что отправляли.
    Проверка идёт по исходному тексту, потому что после замен номера знаков
    съезжают и указать на место в файле стало бы нечем.
    """
    # delimiters: у FPC разделитель только '=', и двоеточие обязано быть
    # обычным ключом. optionxform: иначе ключи-буквы уедут в нижний регистр.
    cfg = configparser.ConfigParser(delimiters=('=',))
    cfg.optionxform = str
    if not cfg.read(path, encoding='utf-8'):
        raise ValueError('нет таблицы подготовки: %s' % path)
    table = {k.upper(): v.upper() for k, v in cfg['replace'].items()}
    out, num = [], 0
    for ch in text.upper():
        if ch not in '\r\n':
            num += 1
        if ch in table:
            if report:
                report('подготовка: знак %d  %s -> %s' % (num, ch, table[ch]))
            out.append(table[ch])
        else:
            if ch not in tables.alphabet and ch not in ' \r\n':
                raise ValueError('знак %d (%s): такого знака нет ни на машине, '
                                 'ни в таблице подготовки' % (num, ch))
            out.append(ch)
    return ''.join(out)


def load(machine_path, key_path, wheels_path=None, **kw):
    """Собрать таблицы и ключ. Комплект берётся из ключа, если не задан явно."""
    head, rows = read_card(key_path)
    if wheels_path is None:
        if 'set' not in head:
            raise ValueError('в ключе нет "set = ", а комплект не задан явно')
        wheels_path = os.path.join(os.path.dirname(machine_path),
                                   'wheels-%s.ini' % head['set'])
        tables = Tables(machine_path, wheels_path)
        # переименованный файл не должен молча оказаться другим комплектом
        if tables.wheel_set != head['set']:
            raise ValueError('комплект %s лежит в файле с id %s'
                             % (head['set'], tables.wheel_set))
    else:
        tables = Tables(machine_path, wheels_path)
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

    def process(self, text):
        decoding = self.k.mode == 'decoding'
        out, num = [], 0
        for ch in text:
            if ch in '\r\n':
                continue
            num += 1
            c = self.contact(self.t.index(ch, decoding, num))
            out.append(self.t.letter(c, decoding))
            self.step()
        return ''.join(out)


# ---------------------------------------------------------------------------
def _fmt(values, alphabet=None):
    """Ряд карты: две группы по пять, как в книгах ключей."""
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


def _random_key(tables, mode='coding'):
    """Случайный ключ через генератор и разбор — так самопроверки заодно
    гоняют формат карты, а не только механизм."""
    text = gen_card(tables, tables.wheel_set, card='builtin', position=True)
    head, rows = parse_card(text.splitlines())
    return Key(head, rows, tables, mode=mode)


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
    typable = tables.alphabet.replace(tables.alphabet[tables.space_contact - 1], '') + ' '
    for _ in range(rounds // 4):
        key = _random_key(tables, 'coding')
        msg = ''.join(random.choice(typable) for _ in range(40))
        enc = Machine(tables, key).process(msg)
        key.mode = 'decoding'
        dec = Machine(tables, key).process(enc)
        if dec != msg:
            return 'обратимость нарушена:\n  %s\n  %s' % (msg, dec)
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


def main():
    ap = argparse.ArgumentParser(description='Эталонная модель Фиалки М-125')
    ap.add_argument('--machine', default=MACHINE)
    ap.add_argument('--wheels', help='перебить комплект, заданный в ключе')
    ap.add_argument('--key', help='ключ дня (карта)')
    ap.add_argument('--pos', help='ключ сообщения: 10 букв начальной установки')
    ap.add_argument('--live', type=int, default=30, choices=(10, 26, 30),
                    help='живые контакты: 30 буквы, 26 смешанный, 10 цифры')
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
        tables = Tables(args.machine, wheels)
        sys.stdout.write(gen_positions(tables.alphabet) + '\n' if args.genpos
                         else gen_card(tables, tables.wheel_set))
        return 0

    if args.selftest:
        tables = Tables(args.machine, args.wheels or
                        os.path.join(os.path.dirname(args.machine), 'wheels-6K.ini'))
        print('комплект дисков:', tables.wheel_set)
        structural = selftest(tables)
        print('структурные проверки:', 'ПРОВАЛ — ' + structural if structural else 'OK')
        demo = verify_demo()
        print('сверка с demo/:', demo if demo else 'OK — знак в знак')
        return 1 if (structural or (demo and 'пропущена' not in demo)) else 0

    if not args.key:
        ap.error('нужен --key, --genkey, --genpos или --selftest')
    if args.prepare and args.mode == 'decoding':
        # на шифртексте подготовка подменила бы Й и испортила его
        ap.error('--prepare применяется только к открытому тексту')
    tables, key = load(args.machine, args.key, args.wheels,
                       mode=args.mode, live=args.live, position=args.pos)
    text = open(args.src, encoding='utf-8').read() if args.src else sys.stdin.read()
    # без .strip(): пробел — знак машины, переводы строк process пропускает сам
    text = text.upper()          # регистра на машине нет: «п» и «П» — одна клавиша
    if args.prepare:
        text = prepare(text, tables, report=lambda s: print(s, file=sys.stderr))
    sys.stdout.write(Machine(tables, key).process(text) + '\n')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as err:     # кривой ключ — не повод для трассы
        sys.exit('ошибка: %s' % err)
