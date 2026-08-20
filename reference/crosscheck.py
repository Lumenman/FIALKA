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
TEXT_MODES = ('L', 'M', 'N', 'L')       # рычаг Б/С/Ц
# Раз в пять кругов машина идёт с латинской головкой: шифр тот же, знаки на
# контактах другие, и обе реализации обязаны прочитать их одинаково. Режим
# цифр с ней не берётся: десять клавиш отпирает проводка машины, а цифры
# латинской головки стоят не на них, см. data/head-poland.ini.
LATIN = os.path.join(fialka.ROOT, 'data', 'head-poland.ini')


def pool_for(tables, text_mode, mode):
    """Что можно подать на вход в этом режиме.

    В расшифровании вход — шифртекст, то есть любые тридцать букв: в
    смешанном режиме среди них попадутся ЦФ и БК, и обе реализации обязаны
    одинаково собрать из них регистр.
    """
    if text_mode == 'N':
        return sorted(tables.digits)
    if mode == 'decoding':
        return list(tables.alphabet)
    chars = [ch for ch in tables.alphabet if tables.typable(ch, text_mode)] + [' ']
    if text_mode == 'M':
        chars += sorted(tables.upper_index)
    return chars


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
        # ';' и '=' записаны в таблице токенами, раскрытие сверяется тоже
        ('точка с запятой и равно', ['--prepare'], 'ИТОГ; А=Б'),
        ('подготовка на шифртексте', ['-d', '--prepare'], 'ФСИЮ'),
        # в смешанном цифры и пунктуация набираются, а Ё по-прежнему нет
        ('смешанный без подготовки', ['--mode', 'M'], 'ДОМ 7, ЪЭЙ'),
        ('смешанный с подготовкой', ['--mode', 'M', '--prepare'], 'ЁЖ 5%'),
        ('смешанный: чужой знак', ['--mode', 'M'], 'ПРИВЕТ!'),
        ('цифры: буква', ['--mode', 'N'], '12А'),
        ('цифры группами', ['--mode', 'N'], '24310 24310'),
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
    return bad + check_abbreviate(key)


def check_abbreviate(key):
    """Сокращения: в поставке таблица пуста, поэтому проверка её подставляет.

    Путь к prepare.ini обе реализации знают сами, ключа для него нет, так что
    файл подменяется на время проверки и возвращается назад. Случай с ЧАС и
    ЧАСОВ здесь главный: берётся самое длинное, иначе порядок строк в файле
    решал бы, во что превратится текст.
    """
    saved = open(fialka.PREPARE, encoding='utf-8').read()
    if '[abbreviate]\n' not in saved:
        print('РАСХОЖДЕНИЕ (сокращения): в prepare.ini нет секции [abbreviate]')
        return 1
    filled = saved.replace('[abbreviate]\n', '[abbreviate]\nМИНУТ = МИН\n'
                           'ЧАСОВ = ЧАС\nЧАС = Ч\nКООРДИНАТЫ = КООРД\n')
    args = ['-e', '--key', key, '--prepare']
    text = 'ЖДАТЬ ЧАСОВ ПЯТЬ, КООРДИНАТЫ ЧЕРЕЗ ДЕСЯТЬ МИНУТ.'
    try:
        with open(fialka.PREPARE, 'w', encoding='utf-8') as f:
            f.write(filled)
        pc, po, pe = run(PASCAL, args, text)
        yc, yo, ye = run(PYTHON, args, text)
    finally:
        with open(fialka.PREPARE, 'w', encoding='utf-8') as f:
            f.write(saved)
    if pc or yc:
        print('РАСХОЖДЕНИЕ (сокращения): паскаль код %d, оракул код %d' % (pc, yc))
        return 1
    if (po, pe) != (yo, ye):
        print('РАСХОЖДЕНИЕ (сокращения)\n  паскаль: %s | %s\n  оракул:  %s | %s'
              % (po, pe, yo, ye))
        return 1
    if 'ЧАСОВ -> ЧАС' not in pe or 'ЧАС -> Ч' in pe:
        print('РАСХОЖДЕНИЕ (сокращения): взято не самое длинное\n  %s' % pe)
        return 1
    return 0


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
            # режим цифр гоняет возвратный контур: при 30 живых контактах у
            # рефлектора нет мёртвых и он вообще не срабатывает
            text_mode = TEXT_MODES[i % len(TEXT_MODES)]
            head = LATIN if (i % 5 == 3 and text_mode != 'N') else None
            head_args = ['-H', head] if head else []
            tables = fialka.Tables(fialka.MACHINE,
                                   os.path.join(fialka.ROOT, 'data', 'wheels-%s.ini' % wheel_set),
                                   head)
            # ключи генерируются то одной реализацией, то другой: так каждый
            # разбор читает чужой вывод, а не только свой собственный
            if (i // 2) % 2:      # не i % 2: иначе генератор был бы намертво
                                  # связан с режимом и половина сочетаний выпала
                card = run_pascal(['--genkey', '--set', wheel_set,
                                   '--seed', str(i)] + head_args, '')
                pos = run_pascal(['--genpos', '--seed', str(i)] + head_args, '')
            else:
                card = fialka.gen_card(tables, wheel_set)
                pos = fialka.gen_positions(tables.alphabet)
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(card + '\n')

            text = ''.join(random.choice(pool_for(tables, text_mode, mode))
                           for _ in range(60))

            _, key = fialka.load(fialka.MACHINE, tmp, head_path=head, mode=mode,
                                 text_mode=text_mode, position=pos)
            want = fialka.Machine(tables, key).process(text)
            got = run_pascal(['-d' if mode == 'decoding' else '-e', '-k', tmp,
                              '--pos', pos, '--mode', text_mode] + head_args, text)
            if got != want:
                bad += 1
                print('РАСХОЖДЕНИЕ %s %s %s (головка %s)\n  вход:    %s'
                      '\n  оракул:  %s\n  паскаль: %s'
                      % (wheel_set, mode, text_mode, tables.head_id, text, want, got))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    bad += check_prepare()
    print('сверено %d прогонов плюс подготовка текста, расхождений: %d' % (rounds, bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
