#!/usr/bin/env python3
"""Эталонная модель шифровальной машины Фиалка М-125.

Реализует механизм, восстановленный из M125v5_16eng.exe и сверенный
с Fialka_200.pdf (Reuvers & Simons). Назначение — оракул: медленно, зато
буквально повторяет формулы из документа по механизму, чтобы по нему можно
было сверять быструю реализацию.

Индексация всюду 1-базовая, как в оригинале: контакты 1..30, слоты 1..10.

    python fialka.py --selftest
    python fialka.py --key keys/example.ini --in msg.txt
"""
import argparse
import configparser
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
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

    def index(self, ch, decoding):
        """Знак -> номер контакта. Пробел делит контакт с последней буквой."""
        if ch == ' ' and not decoding:
            return self.space_contact
        return self.alphabet.index(ch) + 1

    def letter(self, contact, decoding):
        """Номер контакта -> знак. В расшифровании контакт пробела печатается пробелом."""
        if contact == self.space_contact and decoding:
            return ' '
        return self.alphabet[contact - 1]


class Key:
    """Установка на день плюс начальная позиция сообщения."""

    FIELDS = ('wheel_order', 'ring', 'core_order', 'core_side',
              'core_offset', 'position')

    def __init__(self, path, tables):
        cfg = configparser.ConfigParser(inline_comment_prefixes=(';',))
        cfg.read(path, encoding='utf-8')
        k = cfg['key']
        self.mode = k.get('mode', 'coding').strip()
        self.live = int(k.get('live', '30'))
        for name in self.FIELDS:
            setattr(self, name, self._ten(k[name], tables))
        for name in ('wheel_order', 'core_order'):
            if sorted(getattr(self, name)[1:]) != list(range(1, SLOTS + 1)):
                raise ValueError('%s должен быть перестановкой 1..%d' % (name, SLOTS))
        card = k.get('card', 'identity').strip()
        if card == 'identity':
            self.card = [0] + list(range(1, N + 1))
        elif card == 'builtin':
            self.card = list(tables.card_builtin)
        else:
            self.card = _row(card)

    @staticmethod
    def _ten(text, tables):
        out = []
        for token in text.split():
            out.append(int(token) if token.isdigit() else tables.alphabet.index(token) + 1)
        if len(out) != SLOTS:
            raise ValueError('ожидалось %d значений, получено %d' % (SLOTS, len(out)))
        return [0] + out


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
        out = []
        for ch in text:
            if ch in '\r\n':
                continue
            c = self.contact(self.t.index(ch, decoding))
            out.append(self.t.letter(c, decoding))
            self.step()
        return ''.join(out)


# ---------------------------------------------------------------------------
def _random_key(tables, mode='coding'):
    order = list(range(1, SLOTS + 1))
    random.shuffle(order)
    cores = list(range(1, SLOTS + 1))
    random.shuffle(cores)
    key = Key.__new__(Key)
    key.mode, key.live = mode, 30
    key.wheel_order = [0] + order
    key.core_order = [0] + cores
    key.core_side = [0] + [random.choice([1, 2]) for _ in range(SLOTS)]
    key.ring = [0] + [random.randint(1, N) for _ in range(SLOTS)]
    key.core_offset = [0] + [random.randint(1, N) for _ in range(SLOTS)]
    key.position = [0] + [random.randint(1, N) for _ in range(SLOTS)]
    key.card = list(tables.card_builtin)
    return key


def selftest(tables, rounds=200):
    """Две проверки, не требующие эталонных данных.

    1. В режиме plain рефлектор тождественный, значит F = A^-1 * I * A = I:
       машина обязана быть сквозной. Ловит любую ошибку в прямом/обратном
       проходе, входном диске, карте и их инверсиях.
    2. Шифрование и расшифрование при одинаковом старте обязаны быть взаимно
       обратны: coding и decoding — взаимно обратные таблицы рефлектора.
    """
    random.seed(20260817)
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
    """Сверка с эталоном: demo/de.txt -> demo/en.txt на ключе 16.08.26, комплект 6K.

    Единственная проверка, которая говорит «механизм совпал с оригиналом»:
    demo снят с M125v5_16eng.exe, а не построен этой моделью.
    """
    root = os.path.join(HERE, os.pardir)
    paths = {
        'machine': os.path.join(HERE, 'data', 'machine-M125-3.ini'),
        'wheels': os.path.join(HERE, 'data', 'wheels-6K.ini'),
        'key': os.path.join(HERE, 'keys', 'kt16_08_26.ini'),
        'plain': os.path.join(root, 'demo', 'de.txt'),
        'cipher': os.path.join(root, 'demo', 'en.txt'),
    }
    missing = [n for n, p in paths.items() if not os.path.exists(p)]
    if missing:
        return 'пропущена (нет: %s)' % ', '.join(missing)
    tables = Tables(paths['machine'], paths['wheels'])
    key = Key(paths['key'], tables)
    plain = open(paths['plain'], encoding='cp1251').read().strip()
    expect = open(paths['cipher'], encoding='cp1251').read().strip()
    got = Machine(tables, key).process(plain)
    if got != expect:
        return 'РАСХОЖДЕНИЕ\n  получено: %s\n  ожидалось: %s' % (got, expect)
    return None


def main():
    ap = argparse.ArgumentParser(description='Эталонная модель Фиалки М-125')
    ap.add_argument('--machine', default=os.path.join(HERE, 'data', 'machine-M125-3.ini'))
    ap.add_argument('--wheels', default=os.path.join(HERE, 'data', 'wheels-6K.ini'))
    ap.add_argument('--key', help='файл ключа (INI)')
    ap.add_argument('--in', dest='src', help='входной файл (иначе stdin)')
    ap.add_argument('--encoding', default='utf-8',
                    help='кодировка входного файла (demo/ лежит в cp1251)')
    ap.add_argument('--selftest', action='store_true', help='структурные проверки')
    args = ap.parse_args()

    tables = Tables(args.machine, args.wheels)
    if args.selftest:
        print('комплект дисков:', tables.wheel_set)
        structural = selftest(tables)
        print('структурные проверки:', 'ПРОВАЛ — ' + structural if structural else 'OK')
        demo = verify_demo()
        print('сверка с demo/:', demo if demo else 'OK — 24/24')
        return 1 if (structural or (demo and 'пропущена' not in demo)) else 0

    if not args.key:
        ap.error('нужен --key или --selftest')
    key = Key(args.key, tables)
    if args.src:
        text = open(args.src, encoding=args.encoding).read()
    else:
        text = sys.stdin.read()
    sys.stdout.write(Machine(tables, key).process(text.strip()) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
