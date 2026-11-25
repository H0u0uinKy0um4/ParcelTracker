# ParcelTracker

[English](#english) · [Česky](#cesky)

## English

ParcelTracker is a **HACS custom integration for Home Assistant**. It reads
forwarded carrier emails from a dedicated IMAP mailbox (Gmail, Proton, Outlook,
Seznam, or any other provider), extracts tracking numbers, queries the carrier
and exposes each parcel as Home Assistant entities. No separate service,
container or add-on is required.

### How it works

1. A dedicated mailbox (any IMAP provider) receives forwarded delivery notices.
2. The integration reads the mailbox over IMAP (standard library, no extra
   requirements) and extracts:
   - `Číslo zásilky` → tracking number (canonical `barcode`),
   - `Číslo balíku` → pickup code (attribute and sensor).
   If the body has no number, the decoded Subject and tracking links are used
   transiently as fallbacks. Nothing from the email is retained verbatim.
3. A pluggable provider queries the carrier. All carriers use **keyless public
   JSON endpoints** — no API key, no HTML scraping:
   - Česká pošta / Balíkovna: `https://b2c.cpost.cz/services/ParcelHistory/getDataAsJson?idParcel=…&language=en`
     (batches of 10) and `https://www.balikovna.cz/o/cpb/package/<code>?lang=en_US`,
   - Allegro One: `https://edge.allegro.pl/ad/tracking?packageNo=<code>`
     (undocumented edge endpoint, GET),
   - Packeta / Zásilkovna: `POST https://tracking.packeta.com/api/getPacketById/<code>/en`
     (the one keyless endpoint that requires POST).
4. Parcels are stored via Home Assistant `Store` and published with the
   canonical `ha-parcel-integrations` shape, so `ha-parcel-aggregator` can
   consume them. **Do not install `ha-ceska-posta` alongside this integration**
   or the same parcels will appear twice.

### Supported carriers

| Carrier | Status | Provider ID |
|---|---|---|
| Česká pošta / Balíkovna | Implemented | `ceska_posta` |
| Allegro One | Implemented (unofficial edge endpoint) | `allegro` |
| Packeta / Zásilkovna | Implemented (POST web endpoint) | `packeta` |
| DPD CZ | Planned | — |
| PPL CZ | Planned | — |
| GLS CZ, Geis, DHL, InPost | Planned | — |

The architecture is multi-carrier from day one: one config entry is one
mailbox, and carriers are independent provider modules. Adding a carrier means
adding one module, one fixture, one docs row and EN/CS strings — no
carrier-specific changes to entities, coordinator or storage.

### Installation

1. In HACS, add this repository as a **custom repository** of type
   *Integration*, then install **ParcelTracker**.
2. Restart Home Assistant.
3. Go to *Settings → Devices & Services → Add Integration → ParcelTracker*.
4. Enter the email address and the password or app password required by your
   provider (e.g. a Google or Microsoft app password, or the Proton Mail Bridge
   password). Enable IMAP in the provider's settings.
5. Enter the provider's IMAP host and port (usually 993 with TLS), plus the
   mailbox (label), poll interval, sender allowlist and an extra tracking regex.
   Set *Remove delivered parcels after days* (default 30, `0` = never). For
   Balíkovna notices, `balikovna.cz` is a useful allowlist entry.

The `exampleMails/` directory contains real forwarded messages used by the
parser tests. Redact personal data in them before publishing the repository.

Credentials are stored in Home Assistant's config entry (`.storage`) and are
never written to entity attributes, logs or diagnostics.

### Entities and events

For each parcel (one device per `carrier:barcode`):

- `sensor.status` — canonical status: `registered`, `in_transit`,
  `out_for_delivery`, `at_pickup_point`, `delivered`, `returning`, `problem`,
  `unknown`;
- `sensor.last_event` — last human-readable carrier event;
- `sensor.pickup_code` — the `Číslo balíku` value, when present;
- `binary_sensor.delivered`;
- `button.remove` — delete this parcel and its device;
- `button.refresh` — manual refresh for the mailbox.

Status is derived from the **highest rank reached**, not the last event, so
late designation records cannot turn a delivered parcel back into
`at_pickup_point`.

Events fired on the HA event bus:
`parceltracker_parcel_registered`, `parceltracker_parcel_status_changed`,
`parceltracker_parcel_delivered`, `parceltracker_parcel_removed`.

Polling is dynamic: 15 min when something is out for delivery, 30 min in
transit, 60 min when idle or at night (00:00–06:00). Email is always polled.

### Cleanup

Delivered parcels are removed automatically after the configured number of
days (30 by default; `0` disables it). The age is taken from `delivered_at`, or
from `first_seen` for older records. The UID cursor is not changed, so deleting
a parcel never causes old mail to be read again.

For manual removal use the per-parcel `button.remove` or call the service:

```yaml
service: parceltracker.remove_parcel
data:
  carrier: ceska_posta
  barcode: DR1234567890C
```

You can also trigger an immediate refresh or clear all delivered parcels:

```yaml
service: parceltracker.scan_now
```

```yaml
service: parceltracker.clear_delivered
```

Removal deletes the stored parcel(s), their entities and devices, and fires
`parceltracker_parcel_removed` with only the `carrier` and `barcode`.

### Privacy and limitations

- Raw email subject and body are never stored in attributes. Only the two
  numbers, carrier data and timestamps are kept.
- The carrier endpoints are public but unofficial (especially Allegro's edge
  endpoint) and may change. A failed request keeps the cached state and sets
  `unknown` rather than breaking the integration.
- These endpoints are intended for senders/recipients checking their own
  parcels. Do not redistribute the data.
- Nothing here sends mail or modifies messages other than optionally marking
  processed messages as read.

### Development

```bash
python -m unittest discover -s tests -v
```

Tests use stdlib stubs and run without Home Assistant installed.

---

## Česky

ParcelTracker je **vlastní integrace Home Assistantu pro HACS**. Čte přeposlané
e-maily od dopravců z vyhrazené IMAP schránky (Gmail, Proton, Outlook, Seznam
nebo jiný poskytovatel), vytěží čísla zásilek, dotáže se dopravce a zobrazí
balíky jako entity v Home Assistantu. Není potřeba žádný samostatný server,
Docker ani doplněk.

### Jak to funguje

1. Do vyhrazené schránky (libovolný IMAP poskytovatel) chodí přeposlaná
   oznámení o doručení.
2. Integrace schránku načte přes IMAP (bez externích závislostí) a vytěží:
   - `Číslo zásilky` → číslo zásilky (kanonické `barcode`),
   - `Číslo balíku` → číslo balíku k vyzvednutí (atribut a senzor).
   Pokud tělo číslo neobsahuje, dočasně se použije dekódovaný předmět a odkazy
   na sledování. Nic z e-mailu se neuchovává doslovně.
3. Provider se dotáže dopravce. Všichni dopravci používají **veřejné JSON
   endpointy bez klíče** — bez API klíče a bez parsování HTML:
   - Česká pošta / Balíkovna: `https://b2c.cpost.cz/services/ParcelHistory/getDataAsJson?idParcel=…&language=en`
     (dávky po 10) a `https://www.balikovna.cz/o/cpb/package/<code>?lang=en_US`,
   - Allegro One: `https://edge.allegro.pl/ad/tracking?packageNo=<code>`
     (neoficiální edge endpoint, GET),
   - Packeta / Zásilkovna: `POST https://tracking.packeta.com/api/getPacketById/<code>/en`
     (jediný keyless endpoint, který vyžaduje POST).
4. Balíky se ukládají přes `Store` a vystavují v kanonickém formátu rodiny
   `ha-parcel-integrations`, takže je umí zpracovat `ha-parcel-aggregator`.
   **Neinstalujte vedle této integrace `ha-ceska-posta`**, jinak se balíky
   zduplikují.

### Podporovaní dopravci

| Dopravce | Stav | ID provideru |
|---|---|---|
| Česká pošta / Balíkovna | Implementováno | `ceska_posta` |
| Allegro One | Implementováno (neoficiální edge endpoint) | `allegro` |
| Packeta / Zásilkovna | Implementováno (POST web endpoint) | `packeta` |
| DPD CZ | Plánováno | — |
| PPL CZ | Plánováno | — |
| GLS CZ, Geis, DHL, InPost | Plánováno | — |

Architektura je od začátku multi-carrier: jedna konfigurační položka = jedna
schránka, dopravci jsou samostatné moduly. Přidání dopravce znamená nový modul,
fixture, řádek v tabulce a překlady EN/CS — entity, koordinátor ani úložiště se
nemění specificky pro dopravce.

### Instalace

1. V HACS přidejte tento repozitář jako **vlastní repozitář** typu
   *Integrace* a nainstalujte **ParcelTracker**.
2. Restartujte Home Assistant.
3. Přejděte do *Nastavení → Zařízení a služby → Přidat integraci → ParcelTracker*.
4. Zadejte e-mailovou adresu a heslo nebo heslo pro aplikaci podle svého
   poskytovatele (např. Google, Microsoft, Proton Mail Bridge). V nastavení
   poskytovatele zapněte IMAP.
5. Zadejte IMAP server a port poskytovatele (obvykle 993 s TLS), schránku
   (štítek), interval, seznam povolených odesílatelů a doplňkový regex pro
   číslo zásilky. Nastavte *Mazat doručené balíky po dnech* (výchozí 30, `0` =
   nikdy). Pro oznámení z Balíkovny se hodí jako povolený odesílatel
   `balikovna.cz`.

Adresář `exampleMails/` obsahuje reálné přeposlané zprávy použité v testech
parseru. Před zveřejněním repozitáře v nich anonymizujte osobní údaje.

Přihlašovací údaje jsou uložené v konfigurační položce Home Assistantu
(`.storage`) a nikdy se nedostanou do atributů entit, logů ani diagnostiky.

### Entity a události

Pro každý balík (jedno zařízení na `carrier:barcode`):

- `sensor.status` — kanonický stav: `registered`, `in_transit`,
  `out_for_delivery`, `at_pickup_point`, `delivered`, `returning`, `problem`,
  `unknown`;
- `sensor.last_event` — poslední událost v lidském jazyce;
- `sensor.pickup_code` — hodnota `Číslo balíku`, pokud je dostupná;
- `binary_sensor.delivered`;
- `button.remove` — smaže tento balík a jeho zařízení;
- `button.refresh` — ruční obnovení schránky.

Stav se odvozuje z **nejvyšší dosažené úrovně**, ne z poslední události, aby
pozdější evidenční záznamy nevrátily doručený balík zpět na `at_pickup_point`.

Události na sběrnici HA:
`parceltracker_parcel_registered`, `parceltracker_parcel_status_changed`,
`parceltracker_parcel_delivered`, `parceltracker_parcel_removed`.

Dotazování je dynamické: 15 min při doručování, 30 min v přepravě, 60 min při
nečinnosti a v noci (00:00–06:00). E-mail se vždy kontroluje.

### Mazání balíků

Doručené balíky se automaticky mažou po nastaveném počtu dnů (výchozí 30, `0`
=vypnuto). Stáří se počítá z `delivered_at`, u starších záznamů z `first_seen`.
Kurzor UID se nemění, takže smazání balíku nezpůsobí opětovné načtení starých
e-mailů.

Pro ruční smazání použijte tlačítko `button.remove` u balíku nebo službu:

```yaml
service: parceltracker.remove_parcel
data:
  carrier: ceska_posta
  barcode: DR1234567890C
```

Můžete také spustit okamžité obnovení nebo vymazat všechny doručené balíky:

```yaml
service: parceltracker.scan_now
```

```yaml
service: parceltracker.clear_delivered
```

Smazání odstraní uložený balík(y), jeho entity i zařízení a vyvolá událost
`parceltracker_parcel_removed` pouze s `carrier` a `barcode`.

### Soukromí a omezení

- Předmět a tělo e-mailu se do atributů nikdy neukládají. Uchováváme jen obě
  čísla, data z dopravce a časové značky.
- Endpointy dopravce jsou veřejné, ale neoficiální (zejména Allegro edge
  endpoint) a mohou se změnit. Při selhání se zachová poslední známý stav a
  integrace nespadne.
- Endpointy jsou určeny pro odesílatele/adresáty kontrolující vlastní balíky.
  Data dál nešířte.
- Integrace neodesílá e-maily a s zprávami nemanipuluje, kromě volitelného
  označení zpracovaných zpráv jako přečtené.

### Vývoj

```bash
python -m unittest discover -s tests -v
```

Testy používají standardní knihovnu a fungují i bez nainstalovaného Home
Assistantu.
