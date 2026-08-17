# Инструменты реверса

`DumpFuncs.java` — headless-скрипт Ghidra: декомпилирует функции, содержащие
указанные адреса, и выводит C в файл. Если Ghidra не создала функцию (в этом
бинарнике так с несколькими — она не разбирает их автоматически), скрипт сам
дизассемблирует адрес и создаёт функцию.

Разовый импорт и анализ:

```bash
GHIDRA=/d/Downloads/ghidra_12.1.2_PUBLIC_20260605
"$GHIDRA/support/analyzeHeadless.bat" <projdir> fialka \
    -import deco/M125v5_16eng.exe \
    -processor "x86:LE:32:default" -cspec borlanddelphi
```

`-cspec` без `-processor` не принимается. Дальше — сколько угодно быстрых
проходов без переанализа:

```bash
"$GHIDRA/support/analyzeHeadless.bat" <projdir> fialka \
    -process "M125v5_16eng.exe" -noanalysis \
    -scriptPath reference/tools -postScript DumpFuncs.java out.c 0x469f74 0x469bf4
```

Ключевые адреса — в документе по механизму, раздел «Карта адресов».
Функции 0x4694a0, 0x46952c, 0x469568 и 0x4696f4 приходится дизассемблировать
принудительно; их входные точки находятся по `call rel32` из 0x469f3c…0x469f58.
