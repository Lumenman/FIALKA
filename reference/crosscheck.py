#!/usr/bin/env python3
"""Сверка реализации на Паскале с эталонной моделью.

Генерирует случайные ключи дня и ключи сообщения, прогоняет один и тот же
текст обеими реализациями и сравнивает знак в знак. Демо проверяет только
одну точку; здесь проверяются все три комплекта, оба режима и произвольные
начальные установки.

    python crosscheck.py [сколько]
"""
import os
import random
import subprocess
import sys

import fialka

EXE = os.path.join(fialka.ROOT, 'pascal', 'fialka.exe')
KEYS = ('3K', '5K', '6K')


def run_pascal(args, text):
    out = subprocess.run([EXE] + args, input=text.encode('utf-8'),
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if out.returncode:
        raise RuntimeError(out.stdout.decode('utf-8', 'replace').strip())
    # только перевод строки: пробел на конце может быть знаком сообщения
    return out.stdout.decode('utf-8').rstrip('\r\n')


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    if not os.path.exists(EXE):
        sys.exit('нет %s — собери: cd pascal && fpc -O2 fialka.pas' % EXE)
    random.seed(20260817)
    tmp = os.path.join(fialka.ROOT, 'pascal', '_crosscheck.key')
    bad = 0
    try:
        for i in range(rounds):
            wheel_set = KEYS[i % len(KEYS)]
            mode = 'decoding' if i % 2 else 'coding'
            tables = fialka.Tables(fialka.MACHINE,
                                   os.path.join(fialka.ROOT, 'data', 'wheels-%s.ini' % wheel_set))
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(fialka.gen_card(tables, wheel_set))
            pos = fialka.gen_positions(tables.alphabet)

            typable = tables.alphabet.replace(tables.alphabet[tables.space_contact - 1], '') + ' '
            text = ''.join(random.choice(typable) for _ in range(60))
            if mode == 'decoding':                 # на расшифрование пробелы не подают
                text = text.replace(' ', tables.alphabet[0])

            _, key = fialka.load(fialka.MACHINE, tmp, mode=mode, position=pos)
            want = fialka.Machine(tables, key).process(text)
            got = run_pascal(['-d' if mode == 'decoding' else '-e',
                              '-k', tmp, '--pos', pos], text)
            if got != want:
                bad += 1
                print('РАСХОЖДЕНИЕ %s %s\n  вход:    %s\n  оракул:  %s\n  паскаль: %s'
                      % (wheel_set, mode, text, want, got))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    print('сверено %d прогонов, расхождений: %d' % (rounds, bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
