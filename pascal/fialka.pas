program fialka;
{
  Шифровальная машина Фиалка М-125.

  Механизм восстановлен из M125v5_16eng.exe и сверен с Fialka_200.pdf
  (Reuvers & Simons). Формулы повторяют эталонную модель reference/fialka.py
  один в один; обе читают одни и те же файлы из data/ и keys/.

  Индексация всюду 1-базовая, как в оригинале: контакты 1..30, слоты 1..10.
  Проверки диапазона и переполнения включены намеренно: на путанице
  1-базового и 0-базового счёта погорели обе ранние реализации на Си.

    fialka --selftest
    fialka -e -k ../keys/kt16_08_26.txt ../demo/de.txt
}
{$mode objfpc}{$H+}{$R+}{$Q+}

uses
  {$IFDEF WINDOWS}Windows,{$ENDIF}
  SysUtils, Classes, IniFiles;

const
  N = 30;                      { контактов на диске }
  SLOTS = 10;                  { дисков в барабане }

type
  TContact = 1..N;
  TWire = array[TContact] of 0..N;      { 0 — нет связи }
  TPins = set of TContact;
  TRow = array[1..SLOTS] of Integer;    { ряд карты }
  TMode = (mCoding, mDecoding, mPlain);

var
  { «Железо»: машина плюс комплект дисков. }
  Alphabet: array[TContact] of string;  { UTF-8, по знаку в элементе }
  SpaceContact, ProbeEven, ProbeOdd: Integer;
  Live: Integer = 30;                   { тумблер Б/С/Ц: живых контактов }
  Entry, EntryInv, CardBuiltin: TWire;
  Keyboard, KeyboardInv: TWire;
  Refl, ReentryIn, ReentryOut: TWire;
  Coding, Decoding, AltCoding, AltDecoding: TWire;
  WheelSet: string;
  Wiring, WiringInv: array[1..SLOTS] of TWire;
  Pins: array[1..SLOTS] of TPins;

  { Ключ дня плюс начальная установка сообщения. }
  Mode: TMode = mCoding;
  WheelOrder, Ring, CoreOrder, CoreSide, CoreOffset, Position: TRow;
  Card, CardInv: TWire;

  { Состояние машины: положение дисков в барабане. }
  WPos: TRow;

procedure Die(const Fmt: string; const Args: array of const);
begin
  WriteLn(StdErr, 'ошибка: ', Format(Fmt, Args));
  Halt(1);
end;

{ ---- мелочи ------------------------------------------------------------- }

function Norm(X: Integer): Integer;
begin
  { привести к 1..N; mod в Паскале даёт отрицательное на отрицательных }
  Result := (X - 1) mod N;
  if Result < 0 then Inc(Result, N);
  Result := Result + 1;
end;

procedure Invert(const Src: TWire; out Dst: TWire);
var
  I: Integer;
begin
  FillChar(Dst, SizeOf(Dst), 0);
  for I := 1 to N do
    if Src[I] <> 0 then Dst[Src[I]] := I;
end;

function Tokens(const S: string): TStringList;
begin
  Result := TStringList.Create;
  ExtractStrings([' ', #9], [], PChar(S), Result);
end;

{ Длина знака UTF-8 по ведущему байту. }
function CharLen(B: Byte): Integer;
begin
  if B < $80 then Result := 1
  else if B shr 5 = %110 then Result := 2
  else if B shr 4 = %1110 then Result := 3
  else Result := 4;
end;

{ Очередной знак строки; I двигается на следующий. }
function NextChar(const S: string; var I: Integer): string;
var
  L: Integer;
begin
  L := CharLen(Byte(S[I]));
  Result := Copy(S, I, L);
  Inc(I, L);
end;

{ Аргументы командной строки Windows отдаёт в системной кодировке, а внутри
  всё в UTF-8. Move вместо присваивания — чтобы FPC не перекодировал обратно
  по метке кодовой страницы. }
function ArgToUtf8(const S: string): string;
var
  U: UTF8String;
begin
  U := UTF8Encode(UnicodeString(S));
  SetLength(Result, Length(U));
  if Length(U) > 0 then Move(U[1], Result[1], Length(U));
end;

function ContactOf(const Ch: string): Integer;
var
  I: Integer;
begin
  for I := 1 to N do
    if Alphabet[I] = Ch then Exit(I);
  Result := 0;
end;

{ Регистра на машине нет: «п» и «П» — одна клавиша, поэтому приводить к
  прописным можно молча. Арифметика по блоку кириллицы в UTF-8: WideUpperCase
  на не-Windows без cwstring тихо ограничивается латиницей, а это ровно тот
  случай, когда тихо неправильно хуже, чем десять строк руками. }
function UpFold(const S: string): string;
var
  I: Integer;
  Ch: string;
  B1, B2: Byte;
begin
  Result := '';
  I := 1;
  while I <= Length(S) do
  begin
    Ch := NextChar(S, I);
    if Length(Ch) = 1 then
    begin
      if (Ch[1] >= 'a') and (Ch[1] <= 'z') then Ch[1] := Chr(Ord(Ch[1]) - 32);
    end
    else if Length(Ch) = 2 then
    begin
      B1 := Byte(Ch[1]);
      B2 := Byte(Ch[2]);
      if (B1 = $D0) and (B2 >= $B0) and (B2 <= $BF) then          { а..п }
        Ch[2] := Chr(B2 - $20)
      else if (B1 = $D1) and (B2 >= $80) and (B2 <= $8F) then     { р..я }
      begin
        Ch[1] := Chr($D0);
        Ch[2] := Chr(B2 + $20);
      end
      else if (B1 = $D1) and (B2 = $91) then                      { ё }
      begin
        Ch[1] := Chr($D0);
        Ch[2] := Chr($81);
      end;
    end;
    Result := Result + Ch;
  end;
end;

function ReadAll(const Path: string): string;
var
  F: TFileStream;
begin
  if not FileExists(Path) then Die('нет файла: %s', [Path]);
  F := TFileStream.Create(Path, fmOpenRead or fmShareDenyNone);
  try
    SetLength(Result, F.Size);
    if F.Size > 0 then F.ReadBuffer(Result[1], F.Size);
  finally
    F.Free;
  end;
end;

{ ---- таблицы ------------------------------------------------------------ }

procedure ParseWire(const S: string; out W: TWire);
var
  L: TStringList;
  I: Integer;
begin
  FillChar(W, SizeOf(W), 0);
  L := Tokens(S);
  try
    if L.Count = 0 then Exit;                  { пустая строка — все нули }
    if L.Count <> N then Die('в таблице ожидалось %d чисел, получено %d', [N, L.Count]);
    for I := 1 to N do W[I] := StrToInt(L[I - 1]);
  finally
    L.Free;
  end;
end;

procedure SplitAlphabet(const S: string);
var
  I, Count, J: Integer;
begin
  I := 1;
  Count := 0;
  while I <= Length(S) do
  begin
    Inc(Count);
    if Count > N then Die('в алфавите больше %d знаков', [N]);
    Alphabet[Count] := NextChar(S, I);
  end;
  if Count <> N then Die('в алфавите %d знаков, а нужно %d', [Count, N]);
  for I := 1 to N do
    for J := I + 1 to N do
      if Alphabet[I] = Alphabet[J] then Die('знак %s в алфавите дважды', [Alphabet[I]]);
end;

procedure LoadMachine(const Path: string; Live: Integer);
var
  Ini: TIniFile;
  Sect: string;
begin
  if not FileExists(Path) then Die('нет файла машины: %s', [Path]);
  Ini := TIniFile.Create(Path);
  try
    SplitAlphabet(Trim(Ini.ReadString('machine', 'alphabet', '')));
    SpaceContact := Ini.ReadInteger('machine', 'space_contact', 0);
    ProbeEven := Ini.ReadInteger('machine', 'probe_even', 0);
    ProbeOdd := Ini.ReadInteger('machine', 'probe_odd', 0);
    if (SpaceContact < 1) or (SpaceContact > N) then Die('space_contact вне 1..%d', [N]);

    ParseWire(Ini.ReadString('entry', 'wiring', ''), Entry);
    Invert(Entry, EntryInv);
    ParseWire(Ini.ReadString('card', 'builtin', ''), CardBuiltin);

    Sect := Format('keyboard.%d', [Live]);
    if not Ini.SectionExists(Sect) then Die('в машине нет секции [%s]', [Sect]);
    ParseWire(Ini.ReadString(Sect, 'wiring', ''), Keyboard);
    Invert(Keyboard, KeyboardInv);

    Sect := Format('reflector.%d', [Live]);
    if not Ini.SectionExists(Sect) then Die('в машине нет секции [%s]', [Sect]);
    ParseWire(Ini.ReadString(Sect, 'coding', ''), Coding);
    ParseWire(Ini.ReadString(Sect, 'decoding', ''), Decoding);
    ParseWire(Ini.ReadString(Sect, 'alt_coding', ''), AltCoding);
    ParseWire(Ini.ReadString(Sect, 'alt_decoding', ''), AltDecoding);
  finally
    Ini.Free;
  end;
end;

procedure LoadWheels(const Path: string);
var
  Ini: TIniFile;
  L: TStringList;
  I, J, V: Integer;
  Seen: TPins;
begin
  if not FileExists(Path) then Die('нет файла комплекта: %s', [Path]);
  Ini := TIniFile.Create(Path);
  try
    WheelSet := Trim(Ini.ReadString('set', 'id', ''));
    for I := 1 to SLOTS do
    begin
      ParseWire(Ini.ReadString(Format('wheel%d', [I]), 'wiring', ''), Wiring[I]);
      Seen := [];
      for J := 1 to N do
      begin
        if Wiring[I][J] = 0 then Die('диск %d: проводка не перестановка 1..%d', [I, N]);
        Include(Seen, Wiring[I][J]);
      end;
      if Seen <> [Low(TContact)..High(TContact)] then
        Die('диск %d: проводка не перестановка 1..%d', [I, N]);
      Invert(Wiring[I], WiringInv[I]);

      Pins[I] := [];
      L := Tokens(Ini.ReadString(Format('wheel%d', [I]), 'pins', ''));
      try
        for J := 0 to L.Count - 1 do
        begin
          V := StrToInt(L[J]);
          if (V < 1) or (V > N) then Die('диск %d: штифт %d вне 1..%d', [I, V, N]);
          Include(Pins[I], V);
        end;
      finally
        L.Free;
      end;
    end;
  finally
    Ini.Free;
  end;
end;

{ ---- подготовка текста --------------------------------------------------- }

var
  PrepFrom, PrepTo: array of string;

procedure LoadPrepare(const Path: string);
var
  Ini: TIniFile;
  L: TStringList;
  I, P: Integer;
begin
  if not FileExists(Path) then Die('нет таблицы подготовки: %s', [Path]);
  Ini := TIniFile.Create(Path);
  L := TStringList.Create;
  try
    Ini.ReadSectionValues('replace', L);
    SetLength(PrepFrom, L.Count);
    SetLength(PrepTo, L.Count);
    for I := 0 to L.Count - 1 do
    begin
      P := Pos('=', L[I]);
      PrepFrom[I] := UpFold(Trim(Copy(L[I], 1, P - 1)));
      PrepTo[I] := UpFold(Trim(Copy(L[I], P + 1, MaxInt)));
    end;
  finally
    L.Free;
    Ini.Free;
  end;
end;

{ Замены документируются построчно: молча менять сообщение нельзя — получатель
  расшифрует не то, что отправляли, и ничто на это не укажет. }
function PrepareText(const S: string): string;
var
  I, J, Num: Integer;
  Ch: string;
  Hit: Boolean;
begin
  Result := '';
  I := 1;
  Num := 0;
  while I <= Length(S) do
  begin
    Ch := NextChar(S, I);
    if (Ch <> #13) and (Ch <> #10) then Inc(Num);
    Hit := False;
    for J := 0 to High(PrepFrom) do
      if PrepFrom[J] = Ch then
      begin
        WriteLn(StdErr, Format('подготовка: знак %d  %s -> %s', [Num, Ch, PrepTo[J]]));
        Result := Result + PrepTo[J];
        Hit := True;
        Break;
      end;
    if not Hit then
    begin
      { проверяем по исходному тексту: после замен номера знаков уже съедут,
        и указывать на место в файле стало бы нечем }
      if (Ch <> ' ') and (Ch <> #13) and (Ch <> #10) and (ContactOf(Ch) = 0) then
        Die('знак %d (%s): такого знака нет ни на машине, ни в таблице подготовки',
            [Num, Ch]);
      Result := Result + Ch;
    end;
  end;
end;

{ ---- ключ дня в формате перфокарты -------------------------------------- }

var
  HeadSet: string = '';
  HeadCard: string = 'identity';
  CardRows: array[1..6] of string;
  CardRowCount: Integer = 0;

{ Строка с '=' — заголовок, строка без — очередной ряд карты (мануал 2.11.1).
  Рядов пять, как на настоящей карте; шестой — прорезь — необязателен: его
  отсутствие означает установку по ряду 1, как при загрузке карты в оригинале. }
procedure ReadCard(const Path: string);
var
  F: TStringList;
  I, P: Integer;
  Line, Name, Value: string;
begin
  if not FileExists(Path) then Die('нет файла ключа: %s', [Path]);
  F := TStringList.Create;
  try
    F.LoadFromFile(Path);
    for I := 0 to F.Count - 1 do
    begin
      Line := F[I];
      P := Pos(';', Line);
      if P > 0 then Line := Copy(Line, 1, P - 1);
      Line := Trim(Line);
      if Line = '' then Continue;
      P := Pos('=', Line);
      if P > 0 then
      begin
        Name := Trim(Copy(Line, 1, P - 1));
        Value := Trim(Copy(Line, P + 1, MaxInt));
        if Name = 'set' then HeadSet := Value
        else if Name = 'card' then HeadCard := Value
        else Die('неизвестный заголовок: %s', [Name]);
      end
      else
      begin
        Inc(CardRowCount);
        if CardRowCount > 6 then
          Die('на карте 5 рядов (6-й, прорезь, необязателен), а их больше', []);
        CardRows[CardRowCount] := Line;
      end;
    end;
  finally
    F.Free;
  end;
  if CardRowCount = 5 then
  begin
    CardRows[6] := CardRows[1];          { нет прорези — равна ряду 1 }
    CardRowCount := 6;
  end
  else if CardRowCount <> 6 then
    Die('на карте 5 рядов (6-й, прорезь, необязателен), получено %d', [CardRowCount]);
end;

procedure ParseRow(const S: string; out R: TRow);
var
  L: TStringList;
  I, V: Integer;
  T: string;
begin
  L := Tokens(S);
  try
    if L.Count <> SLOTS then
      Die('в ряду ключа ожидалось %d значений, получено %d: %s', [SLOTS, L.Count, S]);
    for I := 1 to SLOTS do
    begin
      T := L[I - 1];
      if TryStrToInt(T, V) then R[I] := V
      else
      begin
        V := ContactOf(T);
        if V = 0 then Die('не буква алфавита и не число: %s', [T]);
        R[I] := V;
      end;
    end;
  finally
    L.Free;
  end;
end;

{ Перепутанный или сдвинутый ряд даёт правдоподобный, но неверный шифртекст
  и ничем себя не выдаёт, поэтому проверяется каждый. }
procedure CheckRowPerm(const R: TRow; const Name: string);
var
  Seen: set of 1..SLOTS;
  I: Integer;
begin
  Seen := [];
  for I := 1 to SLOTS do
  begin
    if (R[I] < 1) or (R[I] > SLOTS) then
      Die('%s: значение %d вне 1..%d', [Name, R[I], SLOTS]);
    Include(Seen, R[I]);
  end;
  if Seen <> [1..SLOTS] then Die('%s: ожидалась перестановка 1..%d', [Name, SLOTS]);
end;

procedure CheckRowRange(const R: TRow; const Name: string);
var
  I: Integer;
begin
  for I := 1 to SLOTS do
    if (R[I] < 1) or (R[I] > N) then
      Die('%s: значение %d вне 1..%d', [Name, R[I], N]);
end;

procedure BuildKey(const PosOverride: string);
var
  I: Integer;
begin
  ParseRow(CardRows[1], WheelOrder);
  ParseRow(CardRows[2], Ring);
  ParseRow(CardRows[3], CoreOrder);
  ParseRow(CardRows[4], CoreSide);
  ParseRow(CardRows[5], CoreOffset);
  ParseRow(CardRows[6], Position);
  if PosOverride <> '' then ParseRow(PosOverride, Position);

  CheckRowPerm(WheelOrder, 'ряд 1 (обода)');
  CheckRowPerm(CoreOrder, 'ряд 3 (сердечники)');
  for I := 1 to SLOTS do
    if (CoreSide[I] <> 1) and (CoreSide[I] <> 2) then
      Die('ряд 4 (сторона сердечника): только 1 или 2, получено %d', [CoreSide[I]]);
  CheckRowRange(Ring, 'ряд 2 (кольца)');
  CheckRowRange(CoreOffset, 'ряд 5 (смещение)');
  CheckRowRange(Position, 'прорезь');

  if HeadCard = 'identity' then
    for I := 1 to N do Card[I] := I
  else if HeadCard = 'builtin' then
    Card := CardBuiltin
  else
    ParseWire(HeadCard, Card);
  Invert(Card, CardInv);
end;

{ ---- машина ------------------------------------------------------------- }

procedure Setup;
var
  I: Integer;
begin
  case Mode of
    mPlain:
      begin
        for I := 1 to N do Refl[I] := I;
        FillChar(ReentryIn, SizeOf(ReentryIn), 0);
        FillChar(ReentryOut, SizeOf(ReentryOut), 0);
      end;
    mDecoding:
      begin
        Refl := Decoding;
        { «мёртвые» контакты рефлектора возвращают сигнал в барабан }
        ReentryIn := AltCoding;
        ReentryOut := AltDecoding;
      end;
    else
      begin
        Refl := Coding;
        ReentryIn := AltCoding;
        ReentryOut := AltDecoding;
      end;
  end;
  WPos := Position;
end;

{ проход через один диск }
function Hop(Slot, X: Integer; Back: Boolean): Integer;
var
  P, Off, Side, Core, T: Integer;
begin
  P := WPos[Slot] + Ring[Slot];
  Off := CoreOffset[Slot];
  Side := CoreSide[Slot];
  Core := CoreOrder[Slot];

  X := X + P + 1;
  if Side = 1 then X := N + 1 + Off - X else X := X - Off + 1;
  X := Norm(X);
  if Side = 1 then
  begin
    if Back then T := Wiring[Core][X] else T := WiringInv[Core][X];
    X := (N + 2) - T + Off;
  end
  else
  begin
    if Back then T := WiringInv[Core][X] else T := Wiring[Core][X];
    X := T + Off;
  end;
  Result := Norm(X - P - 2);
end;

{ штифт под щупом }
function PinUnder(Slot: Integer): Boolean;
var
  Probe, P: Integer;
begin
  if Slot mod 2 = 0 then Probe := ProbeEven else Probe := ProbeOdd;
  P := Norm(WPos[Slot] + Ring[Slot] + Probe);
  Result := P in Pins[WheelOrder[Slot]];
end;

{ ---- трасса ------------------------------------------------------------- }

var
  Logging: Boolean = False;
  LogF: TextFile;

function PosLine: string;
var
  I: Integer;
begin
  Result := '';
  for I := 1 to SLOTS do Result := Result + Format('%3d', [WPos[I]]);
end;

{ Крайние слоты щупом не опрашиваются: цепочки идут 9-7-5-3-1 и 2-4-6-8-10,
  а блокировку даёт штифт предыдущего диска цепочки. }
function PinLine: string;
var
  I: Integer;
begin
  Result := '';
  for I := 1 to SLOTS do
    if (I = 1) or (I = SLOTS) then Result := Result + '-'
    else if PinUnder(I) then Result := Result + '#'
    else Result := Result + '.';
end;

{ один такт шагового механизма: две встречные цепочки с накопительной
  штифтовой блокировкой, ведущие слоты 9 и 2 }
procedure Step;
const
  FwdChain: array[1..4] of Integer = (7, 5, 3, 1);
  BwdChain: array[1..4] of Integer = (4, 6, 8, 10);
var
  Fwd, Bwd: array[1..5] of Integer;
  NF, NB, I, S: Integer;
  Blocked: Boolean;
  SF, SB: string;
begin
  NF := 1; Fwd[1] := 9;
  Blocked := False;
  for I := 1 to 4 do
  begin
    S := FwdChain[I];
    Blocked := Blocked or PinUnder(S + 2);
    if not Blocked then begin Inc(NF); Fwd[NF] := S; end;
  end;

  NB := 1; Bwd[1] := 2;
  Blocked := False;
  for I := 1 to 4 do
  begin
    S := BwdChain[I];
    Blocked := Blocked or PinUnder(S - 2);
    if not Blocked then begin Inc(NB); Bwd[NB] := S; end;
  end;

  SF := '';
  for I := 1 to NF do
  begin
    S := Fwd[I];
    if I > 1 then SF := SF + ',';
    SF := SF + IntToStr(S);
    if WPos[S] >= N then WPos[S] := 1 else Inc(WPos[S]);
  end;
  SB := '';
  for I := 1 to NB do
  begin
    S := Bwd[I];
    if I > 1 then SB := SB + ',';
    SB := SB + IntToStr(S);
    if WPos[S] <= 1 then WPos[S] := N else Dec(WPos[S]);
  end;
  if Logging then
    WriteLn(LogF, Format('  шаг    вперёд %-9s назад %-11s поз%s', [SF, SB, PosLine]));
end;

{ один знак: контакт на входе -> контакт на выходе }
function Contact(CIn: Integer): Integer;
var
  I, U, T, Slot, Round: Integer;
  Found: Boolean;
  S: string;
begin
  I := Keyboard[CIn];
  if Logging then WriteLn(LogF, Format('  kb     %d>%d', [CIn, I]));
  Found := False;
  for Round := 1 to N do                     { возвратный контур на входе }
  begin
    if Logging then
      S := Format('  круг %d вниз   card %d>%d  entry %d>%d  диски',
                  [Round, I, Card[I], Card[I], Entry[Card[I]]]);
    U := Entry[Card[I]];
    for Slot := SLOTS downto 1 do
    begin
      U := Hop(Slot, U, False);
      if Logging then S := S + Format(' %d:%d', [Slot, U]);
    end;
    if Refl[U] <> 0 then
    begin
      if Logging then WriteLn(LogF, S, Format('  refl %d>%d', [U, Refl[U]]));
      I := Refl[U];
      Found := True;
      Break;
    end;
    if Logging then
      WriteLn(LogF, S, Format('  refl %d мёртвый, возврат %d', [U, ReentryIn[U]]));
    I := ReentryIn[U];
  end;
  if not Found then Die('вход: сигнал не вышел из контура', []);

  for Round := 1 to N do                     { возвратный контур на выходе }
  begin
    if Logging then S := Format('  круг %d вверх  рефлектор >%d  диски', [Round, I]);
    U := I;
    for Slot := 1 to SLOTS do
    begin
      U := Hop(Slot, U, True);
      if Logging then S := S + Format(' %d:%d', [Slot, U]);
    end;
    T := CardInv[EntryInv[U]];
    if KeyboardInv[T] <> 0 then
    begin
      if Logging then
        WriteLn(LogF, S, Format('  entry'' %d>%d  card'' %d>%d  kb'' %d>%d',
                [U, EntryInv[U], EntryInv[U], T, T, KeyboardInv[T]]));
      Exit(KeyboardInv[T]);
    end;
    if Logging then
      WriteLn(LogF, S, Format('  entry'' %d>%d  card'' %d>%d  контакт мёртвый, возврат %d',
              [U, EntryInv[U], EntryInv[U], T, ReentryOut[T]]));
    I := ReentryOut[T];
  end;
  Die('выход: сигнал не вышел из контура', []);
  Result := 0;
end;

{ Пробел делит контакт с последней буквой алфавита (Й). }
function IndexOfChar(const Ch: string; Num: Integer): Integer;
begin
  if (Ch = ' ') and (Mode <> mDecoding) then Result := SpaceContact
  else
  begin
    Result := ContactOf(Ch);
    if Result = 0 then
      Die('знак %d (%s): такого знака на машине нет, готовьте текст ключом --prepare',
          [Num, Ch]);
    { Й делит контакт с пробелом, поэтому в открытом тексте её набрать нельзя:
      она молча ушла бы в пробел. В шифртексте контакт 30 печатается как Й,
      так что при расшифровании она законна. }
    if (Result = SpaceContact) and (Mode <> mDecoding) then
      Die('знак %d (%s): её клавиша занята пробелом, замените на И (мануал 4.5) '
          + 'или примените --prepare', [Num, Ch]);
  end;
  { при 26 и 10 живых контактах часть клавиш отключена }
  if Keyboard[Result] = 0 then
    Die('знак %d (%s): при %d живых контактах его клавиша отключена',
        [Num, Ch, Live]);
end;

function LetterOf(C: Integer): string;
begin
  if (C = SpaceContact) and (Mode = mDecoding) then Exit(' ');
  Result := Alphabet[C];
end;

function Process(const Text: string): string;
var
  I, Num, CIn, COut: Integer;
  Ch: string;
begin
  Result := '';
  I := 1;
  Num := 0;
  while I <= Length(Text) do
  begin
    Ch := NextChar(Text, I);
    if (Ch = #13) or (Ch = #10) then Continue;
    Inc(Num);
    CIn := IndexOfChar(Ch, Num);
    if Logging then
    begin
      WriteLn(LogF);
      WriteLn(LogF, Format('--- знак %d: %s = контакт %d', [Num, Ch, CIn]));
      WriteLn(LogF, Format('  поз   %s   щупы %s', [PosLine, PinLine]));
    end;
    COut := Contact(CIn);
    Result := Result + LetterOf(COut);
    if Logging then
      WriteLn(LogF, Format('  =      контакт %d = %s', [COut, LetterOf(COut)]));
    Step;
  end;
end;

{ ---- самопроверки ------------------------------------------------------- }

procedure RandomPerm(out R: TRow);
var
  I, J, T: Integer;
begin
  for I := 1 to SLOTS do R[I] := I;
  for I := SLOTS downto 2 do                 { тасовка Фишера — Йетса }
  begin
    J := 1 + Random(I);
    T := R[I]; R[I] := R[J]; R[J] := T;
  end;
end;

procedure RandomRow(out R: TRow; Hi: Integer);
var
  I: Integer;
begin
  for I := 1 to SLOTS do R[I] := 1 + Random(Hi);
end;

procedure RandomKey;
begin
  RandomPerm(WheelOrder);
  RandomPerm(CoreOrder);
  RandomRow(CoreSide, 2);
  RandomRow(Ring, N);
  RandomRow(CoreOffset, N);
  RandomRow(Position, N);
  Card := CardBuiltin;
  Invert(Card, CardInv);
end;

{ Ряд карты: две группы по пять, как в книгах ключей. }
function FmtRow(const R: TRow; AsLetters: Boolean): string;
var
  I: Integer;
  Left, Right, V: string;
begin
  Left := '';
  Right := '';
  for I := 1 to SLOTS do
  begin
    if AsLetters then V := Alphabet[R[I]] else V := IntToStr(R[I]);
    if I <= 5 then
    begin
      if I > 1 then Left := Left + ' ';
      Left := Left + V;
    end
    else
    begin
      if I > 6 then Right := Right + ' ';
      Right := Right + V;
    end;
  end;
  Result := Format('%-9s   %s', [Left, Right]);
end;

{ Ключ сообщения: начальная установка десяти дисков (мануал, гл. 2.11.2). }
function MakePositions: string;
var
  R: TRow;
begin
  RandomRow(R, N);
  Result := FmtRow(R, True);
end;

{ Случайный ключ дня. Прорези на карте нет: она приходит из книги ключей
  сообщения, а не с карты. }
function MakeCard(const SetId: string): string;
var
  R: TRow;

  function Line(const Comment: string; AsLetters: Boolean): string;
  begin
    Result := FmtRow(R, AsLetters) + '   ; ' + Comment + LineEnding;
  end;

begin
  Result := '; ключ дня, комплект ' + SetId + LineEnding +
            'set  = ' + SetId + LineEnding +
            'card = identity' + LineEnding + LineEnding;
  RandomPerm(R);    Result := Result + Line('1 обода по слотам', True);
  RandomRow(R, N);  Result := Result + Line('2 кольца', True);
  RandomPerm(R);    Result := Result + Line('3 сердечники по слотам', True);
  RandomRow(R, 2);  Result := Result + Line('4 сторона сердечника', False);
  RandomRow(R, N);  Result := Result + Line('5 смещение сердечника', True);
end;

{ 1. В режиме plain рефлектор тождественный, значит F = A^-1 * I * A = I:
     машина обязана быть сквозной. Ловит любую ошибку в прямом и обратном
     проходе, во входном диске, в перфокарте и в построении обратных таблиц. }
function TestPlain(Rounds: Integer): string;
var
  R, C, Got: Integer;
begin
  Mode := mPlain;
  for R := 1 to Rounds do
  begin
    RandomKey;
    Setup;
    for C := 1 to N do
    begin
      Got := Contact(C);
      if Got <> C then
        Exit(Format('plain не сквозной: %d -> %d', [C, Got]));
    end;
  end;
  Result := '';
end;

{ 2. coding и decoding — взаимно обратные таблицы рефлектора, поэтому
     шифрование и расшифрование при одинаковом старте взаимно обратны.
     Й набрать нельзя, её клавиша занята пробелом, поэтому в открытый текст
     она не попадает (мануал, гл. 4.5). }
function TestReversible(Rounds: Integer): string;
var
  R, I, C: Integer;
  Msg, Enc, Dec: string;
begin
  for R := 1 to Rounds do
  begin
    RandomKey;
    Msg := '';
    for I := 1 to 40 do
    begin
      C := 1 + Random(N);
      if C = SpaceContact then Msg := Msg + ' ' else Msg := Msg + Alphabet[C];
    end;
    Mode := mCoding;
    Setup;
    Enc := Process(Msg);
    Mode := mDecoding;
    Setup;
    Dec := Process(Enc);
    if Dec <> Msg then
      Exit(Format('обратимость нарушена:'#10'  %s'#10'  %s', [Msg, Dec]));
  end;
  Result := '';
end;

{ 3. Регрессионный вектор: шифртекст в demo/en.txt построен этой реализацией,
     поэтому проверка стережёт неизменность поведения, но не доказывает
     совпадение с оригиналом. См. demo/note.txt. }
function TestDemo(const MachinePath, KeyPath, PlainPath, CipherPath: string): string;
var
  Plain, Expect, Got: string;
begin
  if not (FileExists(KeyPath) and FileExists(PlainPath) and FileExists(CipherPath)) then
    Exit('пропущена (нет файлов demo/)');
  ReadCard(KeyPath);
  if HeadSet = '' then Exit('в ключе demo нет "set ="');
  LoadWheels(ExtractFilePath(MachinePath) + Format('wheels-%s.ini', [HeadSet]));
  BuildKey('');
  Mode := mCoding;
  Setup;
  Plain := Trim(ReadAll(PlainPath));
  Expect := Trim(ReadAll(CipherPath));
  Got := Process(Plain);
  if Got <> Expect then
    Exit(Format('РАСХОЖДЕНИЕ'#10'  получено:  %s'#10'  ожидалось: %s', [Got, Expect]));
  Result := '';
end;

{ ---- консольный интерфейс ----------------------------------------------- }

function Near(const Name: string): string;
begin
  Result := ExtractFilePath(ParamStr(0)) + '..' + DirectorySeparator + Name;
end;

procedure Usage;
begin
  WriteLn('Фиалка М-125');
  WriteLn;
  WriteLn('  fialka [-e|-d|-p] -k ключ.txt [ключи] [вход.txt]');
  WriteLn('  fialka --genkey | --genpos | --selftest');
  WriteLn;
  WriteLn('  -e, -d, -p      зашифрование (по умолчанию), расшифрование, сквозной прогон');
  WriteLn('  -k, --key ФАЙЛ  ключ дня в формате перфокарты');
  WriteLn('  --pos "..."     ключ сообщения: 10 букв начальной установки');
  WriteLn('  --live N        живые контакты: 30 буквы, 26 смешанный, 10 цифры');
  WriteLn('  -M, --machine   файл машины (по умолчанию ../data/machine-M125-3.ini)');
  WriteLn('  -w, --wheels    перебить комплект, заданный в ключе');
  WriteLn('  -o ФАЙЛ         выходной файл (иначе stdout)');
  WriteLn('  --log ФАЙЛ      подробная трасса: позиции, щупы, круги, шаг');
  WriteLn('  --prepare       подготовить текст по data/prepare.ini, с отчётом');
  WriteLn;
  WriteLn('  --genkey        случайный ключ дня (комплект задаётся --set)');
  WriteLn('  --genpos        случайный ключ сообщения');
  WriteLn('  --set ID        комплект дисков для --genkey (по умолчанию 6K)');
  WriteLn('  --seed N        зерно ГПСЧ, для воспроизводимости');
  Halt(0);
end;

var
  MachinePath, WheelsPath, KeyPath, PosArg, OutPath, InPath, LogPath,
    Text, Res, Err, SetArg: string;
  Argc, Seed: Integer;
  SelfTest: Boolean = False;
  GenKey: Boolean = False;
  GenPos: Boolean = False;
  HasSeed: Boolean = False;
  Preparing: Boolean = False;
  OutFile: TextFile;

procedure Emit(const S: string);
begin
  if OutPath = '' then WriteLn(S)
  else
  begin
    AssignFile(OutFile, OutPath);
    Rewrite(OutFile);
    SetTextCodePage(OutFile, DefaultSystemCodePage);
    WriteLn(OutFile, S);
    CloseFile(OutFile);
  end;
end;

function NextArg(var I: Integer): string;
begin
  Inc(I);
  if I > Argc then Die('у ключа %s нет значения', [ParamStr(I - 1)]);
  Result := ParamStr(I);
end;

procedure ParseArgs;
var
  I: Integer;
  A: string;
begin
  I := 1;
  Argc := ParamCount;
  while I <= Argc do
  begin
    A := ParamStr(I);
    if A = '-e' then Mode := mCoding
    else if A = '-d' then Mode := mDecoding
    else if A = '-p' then Mode := mPlain
    else if (A = '-k') or (A = '--key') then KeyPath := NextArg(I)
    else if (A = '-M') or (A = '--machine') then MachinePath := NextArg(I)
    else if (A = '-w') or (A = '--wheels') then WheelsPath := NextArg(I)
    else if A = '--pos' then PosArg := ArgToUtf8(NextArg(I))
    else if A = '--live' then Live := StrToInt(NextArg(I))
    else if A = '-o' then OutPath := NextArg(I)
    else if A = '--log' then LogPath := NextArg(I)
    else if A = '--selftest' then SelfTest := True
    else if A = '--prepare' then Preparing := True
    else if A = '--genkey' then GenKey := True
    else if A = '--genpos' then GenPos := True
    else if A = '--set' then SetArg := NextArg(I)
    else if A = '--seed' then
    begin
      Seed := StrToInt(NextArg(I));
      HasSeed := True;
    end
    else if (A = '-h') or (A = '--help') then Usage
    else if (Length(A) > 0) and (A[1] = '-') then Die('неизвестный ключ: %s', [A])
    else InPath := A;
    Inc(I);
  end;
end;

procedure RunSelfTest;
var
  Bad: string;
begin
  LoadWheels(ExtractFilePath(MachinePath) + 'wheels-6K.ini');
  WriteLn('комплект дисков: ', WheelSet);

  RandSeed := 20260817;
  Bad := TestPlain(200);
  if Bad = '' then Bad := TestReversible(50);
  if Bad = '' then WriteLn('структурные проверки: OK')
  else WriteLn('структурные проверки: ПРОВАЛ — ', Bad);

  Err := TestDemo(MachinePath, Near('keys' + DirectorySeparator + 'kt16_08_26.txt'),
                  Near('demo' + DirectorySeparator + 'de.txt'),
                  Near('demo' + DirectorySeparator + 'en.txt'));
  if Err = '' then WriteLn('сверка с demo/: OK — знак в знак')
  else WriteLn('сверка с demo/: ', Err);

  if (Bad <> '') or ((Err <> '') and (Pos('пропущена', Err) = 0)) then Halt(1);
end;

var
  Line: string;
begin
  { Всё в UTF-8, включая консоль. Строки внутри программы — сырые байты UTF-8,
    поэтому любая перекодировка при вводе-выводе их только испортит: codepage
    текстовых файлов приравнивается к системному, и FPC ничего не трогает. }
  {$IFDEF WINDOWS}
  SetConsoleOutputCP(65001);
  SetConsoleCP(65001);
  {$ENDIF}
  SetTextCodePage(Output, DefaultSystemCodePage);
  SetTextCodePage(StdErr, DefaultSystemCodePage);
  SetTextCodePage(Input, DefaultSystemCodePage);
  MachinePath := Near('data' + DirectorySeparator + 'machine-M125-3.ini');
  WheelsPath := '';
  KeyPath := '';
  PosArg := '';
  OutPath := '';
  InPath := '';
  LogPath := '';
  SetArg := '6K';
  Live := 30;

  ParseArgs;
  { На шифртексте подготовка подменила бы Й и испортила его. }
  if Preparing and (Mode = mDecoding) then
    Die('--prepare применяется только к открытому тексту', []);
  LoadMachine(MachinePath, Live);

  if SelfTest then
  begin
    RunSelfTest;
    Halt(0);
  end;

  if GenKey or GenPos then
  begin
    { ponytail: Random в FPC — не криптографический ГПСЧ. Для реконструкции
      музейной машины годится; для боевого ключа менять источник, не формат. }
    if HasSeed then RandSeed := Seed else Randomize;
    if GenPos then Emit(MakePositions)
    else
    begin
      if WheelsPath = '' then
        WheelsPath := ExtractFilePath(MachinePath) + Format('wheels-%s.ini', [SetArg]);
      LoadWheels(WheelsPath);            { заодно проверка, что комплект есть }
      Emit(MakeCard(WheelSet));
    end;
    Halt(0);
  end;

  if KeyPath = '' then Usage;

  ReadCard(KeyPath);
  if WheelsPath = '' then
  begin
    if HeadSet = '' then Die('в ключе нет "set = ", а комплект не задан явно', []);
    LoadWheels(ExtractFilePath(MachinePath) + Format('wheels-%s.ini', [HeadSet]));
    { переименованный файл не должен молча оказаться другим комплектом }
    if WheelSet <> HeadSet then
      Die('комплект %s лежит в файле с id %s', [HeadSet, WheelSet]);
  end
  else
    LoadWheels(WheelsPath);

  BuildKey(PosArg);
  Setup;

  if LogPath <> '' then
  begin
    AssignFile(LogF, LogPath);
    Rewrite(LogF);
    SetTextCodePage(LogF, DefaultSystemCodePage);
    Logging := True;
    case Mode of
      mCoding: Res := 'зашифрование';
      mDecoding: Res := 'расшифрование';
      else Res := 'сквозной прогон';
    end;
    WriteLn(LogF, Format('ключ %s, комплект %s, %s, живых контактов %d',
                         [KeyPath, WheelSet, Res, Live]));
    WriteLn(LogF, Format('старт %s', [PosLine]));
  end;

  if InPath <> '' then Text := ReadAll(InPath)
  else
  begin
    Text := '';
    while not Eof(Input) do
    begin
      ReadLn(Input, Line);
      Text := Text + Line;
    end;
  end;
  { Trim здесь недопустим: пробел — знак машины, а не отступ.
    Переводы строк Process пропускает сам. }
  Text := UpFold(Text);
  if Preparing then
  begin
    LoadPrepare(Near('data' + DirectorySeparator + 'prepare.ini'));
    Text := PrepareText(Text);
  end;
  Res := Process(Text);

  if Logging then CloseFile(LogF);
  Emit(Res);
end.
