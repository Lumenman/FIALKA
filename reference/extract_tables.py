#!/usr/bin/env python3
"""Извлекает таблицы Фиалки из M125v5_16eng.exe в плоские INI-файлы.

Одноразовый инструмент: запускать только чтобы перегенерировать data/*.ini.
Адреса виртуальные, imagebase 0x400000, секция DATA 0x473000 (файл 0x72200).
Разбор адресов — см. документ по механизму, раздел «Карта адресов».
"""
import os
import struct
import sys

DATA_RAW, DATA_VA = 0x72200, 0x473000

WHEEL_SETS = {                      # id серии -> (проводка, штифты, страна)
    '3K': (0x4748f8, 0x474da8, 'PL'),
    '5K': (0x474ed4, 0x475384, 'HU'),
    '6K': (0x4754b0, 0x475960, 'CZ'),
}
KEYBOARD = {30: 0x4742e0, 26: 0x474268, 10: 0x4741f0}
REFLECTOR = {                       # живых контактов -> (основная пара, альт. пара)
    30: (0x4743d0, None),
    26: (0x4744c0, 0x4746a0),
    10: (0x4745b0, 0x474790),
}
ENTRY_DISC = 0x474358
CARD_DEFAULT = 0x474880
ALPHABET = 'АБВГДЕЖЗИКЛМНОПРСТУФХЦЧШЩЫЬЮЯЙ'


def load(path):
    with open(path, 'rb') as fh:
        return fh.read()


def u32_row(blob, va, n=30):
    off = DATA_RAW + (va - DATA_VA)
    return [struct.unpack_from('<I', blob, off + 4 * i)[0] for i in range(n)]


def pin_rows(blob, va):
    off = DATA_RAW + (va - DATA_VA)
    raw = blob[off:off + 300]
    return [[i + 1 for i in range(30) if raw[30 * w + i]] for w in range(10)]


def fmt(values):
    return ' '.join(str(v) for v in values)


def write_machine(blob, path):
    lines = [
        '; Машина M-125-3, извлечено из M125v5_16eng.exe',
        '; Все таблицы 1-базовые: значение — номер контакта 1..30.',
        '',
        '[machine]',
        'model         = M-125-3',
        'script        = cyrillic',
        'alphabet      = ' + ALPHABET,
        '; пробел делит контакт с последней буквой алфавита (Й)',
        'space_contact = 30',
        '; позиции щупов блокировки: чётные и нечётные слоты',
        'probe_even    = 16',
        'probe_odd     = 19',
        'slots         = 10',
        'contacts      = 30',
        '',
        '[entry]',
        'wiring = ' + fmt(u32_row(blob, ENTRY_DISC)),
        '',
        '[card]',
        '; перфокарта по умолчанию, зашитая в программу',
        'builtin = ' + fmt(u32_row(blob, CARD_DEFAULT)),
    ]
    for live, va in sorted(KEYBOARD.items()):
        lines += ['', '[keyboard.%d]' % live, 'wiring = ' + fmt(u32_row(blob, va))]
    for live, (main, alt) in sorted(REFLECTOR.items()):
        lines += ['', '[reflector.%d]' % live,
                  'coding   = ' + fmt(u32_row(blob, main)),
                  'decoding = ' + fmt(u32_row(blob, main + 0x78))]
        if alt is None:
            lines += ['alt_coding   =', 'alt_decoding =']
        else:
            lines += ['alt_coding   = ' + fmt(u32_row(blob, alt)),
                      'alt_decoding = ' + fmt(u32_row(blob, alt + 0x78))]
    write(path, lines)


def write_wheels(blob, name, path):
    wiring_va, pins_va, country = WHEEL_SETS[name]
    pins = pin_rows(blob, pins_va)
    lines = ['; Комплект дисков %s (%s), извлечено из M125v5_16eng.exe' % (name, country),
             '; pins — номера контактов, на которых стоит штифт блокировки.',
             '', '[set]', 'id      = ' + name, 'country = ' + country]
    for w in range(10):
        lines += ['', '[wheel%d]' % (w + 1),
                  'wiring = ' + fmt(u32_row(blob, wiring_va + 0x78 * w)),
                  'pins   = ' + fmt(pins[w])]
    write(path, lines)


def write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    print('записано:', path)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    exe = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, os.pardir, 'deco',
                                                             'M125v5_16eng.exe')
    blob = load(exe)
    data = os.path.join(here, 'data')
    write_machine(blob, os.path.join(data, 'machine-M125-3.ini'))
    for name in WHEEL_SETS:
        write_wheels(blob, name, os.path.join(data, 'wheels-%s.ini' % name))


if __name__ == '__main__':
    main()
