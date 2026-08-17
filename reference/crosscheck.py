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
LIVE = (30, 26, 10, 30)


HERE = os.path.dirname(os.path.abspath(__file__))
PASCAL = [EXE]
PYTHON = [sys.executable, os.path.join(HERE, 'fialka.py')]


def run(cmd, args, text):
    """-> (код возврата, stdout, stderr). Потоки раздельно: отчёт подготовки
    идёт в stderr и сверяется отдельно от шифртекста."""
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    p = subprocess.run(cmd + args, input=text.encode('utf-8'), env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # rstrip только по переводу строки: пробел на конце может быть знаком
    return (p.returncode, p.stdout.decode('utf-8', 'replace').rstrip('\r\n'),
            p.stderr.decode('utf-8', 'replace').strip())


def run_pascal(args, text):
    code, out, err = run(PASCAL, args, text)
    if code:
        raise RuntimeError(err or out)
    return out


def check_prepare():
    """Подготовка текста и отказы должны совпадать в обеих реализациях.

    Основной цикл гоняет только чистый алфавит, поэтому ни замены, ни отказы
    в него не попадают, а разъехаться они могут независимо от механизма.
    """
    key = os.path.join(fialka.ROOT, 'keys', 'kt16_08_26.txt')
    cases = [
        ('подготовка', ['--prepare'], 'Привет, мир: это ёж - "точно".'),
        ('регистр молча вверх', [], 'привет мир'),
        ('Й в открытом тексте', [], 'ЙОД'),
        ('цифра', ['--prepare'], 'ДОМ 7'),
        ('знака нет в таблице', ['--prepare'], 'ПРИВЕТ!'),
        ('подготовка на шифртексте', ['-d', '--prepare'], 'ФСИЮ'),
    ]
    bad = 0
    for name, extra, text in cases:
        args = ['-e', '--key', key] + extra if '-d' not in extra else ['--key', key] + extra
        pc, po, pe = run(PASCAL, args, text)
        yc, yo, ye = run(PYTHON, args, text)
        if (pc == 0) != (yc == 0):
            print('РАСХОЖДЕНИЕ (%s): паскаль код %d, оракул код %d' % (name, pc, yc))
            bad += 1
        elif pc == 0 and (po, pe) != (yo, ye):
            print('РАСХОЖДЕНИЕ (%s)\n  паскаль: %s | %s\n  оракул:  %s | %s'
                  % (name, po, pe, yo, ye))
            bad += 1
    return bad


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
            # 26 и 10 живых контактов гоняют возвратный контур: при 30 у
            # рефлектора нет мёртвых контактов и он вообще не срабатывает
            live = LIVE[i % len(LIVE)]
            tables = fialka.Tables(fialka.MACHINE,
                                   os.path.join(fialka.ROOT, 'data', 'wheels-%s.ini' % wheel_set))
            # ключи генерируются то одной реализацией, то другой: так каждый
            # разбор читает чужой вывод, а не только свой собственный
            if (i // 2) % 2:      # не i % 2: иначе генератор был бы намертво
                                  # связан с режимом и половина сочетаний выпала
                card = run_pascal(['--genkey', '--set', wheel_set, '--seed', str(i)], '')
                pos = run_pascal(['--genpos', '--seed', str(i)], '')
            else:
                card = fialka.gen_card(tables, wheel_set)
                pos = fialka.gen_positions(tables.alphabet)
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(card + '\n')

            # набрать можно только то, чья клавиша подключена при этом live
            kb = tables.keyboard[live]
            typable = ''.join(tables.alphabet[c - 1] for c in range(1, fialka.N + 1)
                              if kb[c] and c != tables.space_contact)
            if mode == 'coding' and kb[tables.space_contact]:
                typable += ' '
            text = ''.join(random.choice(typable) for _ in range(60))

            _, key = fialka.load(fialka.MACHINE, tmp, mode=mode, live=live, position=pos)
            want = fialka.Machine(tables, key).process(text)
            got = run_pascal(['-d' if mode == 'decoding' else '-e', '-k', tmp,
                              '--pos', pos, '--live', str(live)], text)
            if got != want:
                bad += 1
                print('РАСХОЖДЕНИЕ %s %s\n  вход:    %s\n  оракул:  %s\n  паскаль: %s'
                      % (wheel_set, mode, text, want, got))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    bad += check_prepare()
    print('сверено %d прогонов плюс подготовка текста, расхождений: %d' % (rounds, bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
