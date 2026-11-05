# e1watch

Konsolowy monitor **łączy E1/T1 PRI i kanałów B ISDN** dla Asteriska z DAHDI.
Jeden ekran pokazuje stan łącza dla każdego spanu, kanał D, liczniki błędów i każdy kanał B (wolny / zajęty / wołanie / połączony) wraz z numerami obu uczestników rozmowy.

English version: [README.md](README.md)

## Funkcje

- **Kolor łącza = stan**: zielony (sygnał OK, ISDN aktywny), żółty (sygnał OK, kanał D nieaktywny), czerwony (brak sygnału / alarm). Czerwony tylko dla awarii; połączenie nawiązane jest niebieskie.
- Siatka kanałów B z kręcącym się symbolem i migającym wierszem przy wołaniu, pasek obciążenia, wykres historii, dziennik zdarzeń z rozbłyskiem nowych wpisów.
- Numery obu stron, kierunek (PSTN→PBX / PBX→PSTN), czas rozmowy; numery zewnętrzne domyślnie maskowane.
- Spany i kanały B wykrywane automatycznie z `/proc/dahdi` (bez kanału D); nazwy i listy kanałów można nadpisać.
- Działa przez SSH i na konsoli Linuksa (tryb ASCII); klawisze F dekodowane dla xterm, PuTTY, konsoli Linuksa i Windows Terminal; alternatywy: `Esc`+cyfra i litery.
- Interfejs po angielsku i polsku. Wymaga tylko Pythona 3.8+ (bez zależności).

## Instalacja

```bash
pip install .
```

## Konfiguracja Asteriska

`CoreShowChannels` wymaga użytkownika AMI z uprawnieniem `write`. W `manager.conf` (AMI tylko na localhost):

```ini
[e1watch]
secret = <wybierz-haslo>
deny = 0.0.0.0/0.0.0.0
permit = 127.0.0.1/255.255.255.255
read = system,reporting
write = system,reporting
```

Skopiuj [examples/e1watch.conf](examples/e1watch.conf) do `/etc/e1watch.conf` (tryb 600, jeśli zawiera hasło, albo użyj `secret_file`). Ustaw `lang = pl`. Uruchamiaj jako użytkownik z dostępem do `/proc/dahdi` i `asterisk -rx` (zwykle root).

## Użycie

```bash
sudo e1watch                 # ekran na żywo
e1watch --once               # jedna klatka tekstem (--size 120x35, --ascii)
e1watch --demo [ok|alarm|d]  # dane symulowane, bez Asteriska
e1watch --lang pl --no-mask
```

Klawisze: `F1`/`?` pomoc, `F2`/`m` maska numerów, `F3`/`s` kolejne spany, `F4`/`z` zdarzenia, `F5`/`r` odświeżenie, `F10`/`q` wyjście; `Esc`+`1..0` = F1..F10.

## Testy

```bash
python3 -m unittest discover -s tests -v
```

## Licencja

GPL-3.0-or-later, patrz [LICENSE](LICENSE).
