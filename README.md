# Doremi DCP-2000 für Home Assistant

Custom Integration zur Steuerung von Doremi DCP-2000 Kinoservern über das
Netzwerk – Einrichtung komplett über die Oberfläche, beliebig viele Server pro
Home-Assistant-Instanz.

## Installation

### Über HACS (empfohlen bei mehreren HA-Instanzen)

1. Diesen Ordner als eigenes Git-Repository ablegen (GitHub, Gitea, …) und in
   `custom_components/doremi_dcp2000/manifest.json` die Links `documentation`
   und `issue_tracker` anpassen.
2. In jeder HA-Instanz: **HACS → ⋮ → Benutzerdefinierte Repositories** →
   Repository-URL, Typ *Integration* → herunterladen → HA neu starten.
3. Updates verteilt ihr danach zentral: `version` im Manifest erhöhen, Release
   taggen, HACS bietet das Update in allen Instanzen an.

### Manuell

Ordner `custom_components/doremi_dcp2000` nach `/config/custom_components/`
kopieren und HA neu starten.

## Einrichtung

**Einstellungen → Geräte & Dienste → Integration hinzufügen → Doremi DCP-2000**

| Feld | Bedeutung |
|------|-----------|
| Host | IP-Adresse des Servers |
| Port | Steuerport, Standard **5000** |
| Name | z. B. „Kino 1“ – wird Gerätename und Präfix der Entitäten |

Pro Saal einfach erneut hinzufügen. Der Server wird über seine Seriennummer
erkannt; doppelte Einrichtung wird abgelehnt. IP-Wechsel: **⋮ → Neu konfigurieren**.

### Optionen (⋮ → Optionen)

| Option | Standard | Wirkung |
|--------|----------|---------|
| Abfrageintervall | 2 s | Polling von Status, Frame, Clip |
| Standard-Bildrate | 24 | Umrechnung Frames ↔ Sekunden, solange KLV aus ist |
| GPIO-Entitäten | aus | 8 GPI-Binärsensoren + 8 GPO-Schalter + GPI-Byte |
| CPL/SPL-API (KLV) | aus | SPL-Steuerung, CPL-Titel in der Quellenliste, echte Bildrate |
| KLV-Port | 11730 | Wird beim Speichern getestet |

## Entitäten

- **Media Player** – Play / Pause / Stop / Spulen (Frame-genau), Quellenliste =
  alle Clips auf dem Server (Quelle wählen = Clip laden). Mit KLV zusätzlich
  „Nächster/Vorheriger Titel“ = nächstes/vorheriges CPL der SPL.
- **Sensoren** – Transport (Wiedergabe/Pause/Geladen/Gestoppt), Titel, Clip-ID,
  CPL-UUID, Timecode, Fortschritt %, Restzeit, Anzahl Clips; mit KLV:
  SPL-Status, SPL-Fortschritt, SPL-UUID.
- **Buttons** – Dowser öffnen/schließen, Projektor an/aus (generische
  Projektorbefehle `43.C8.80…` über den Server), Frame zurück, Bibliothek
  aktualisieren; mit KLV: SPL abspielen/pausieren/auswerfen.
- **Binärsensoren** – Verbunden; GPI 1–8 (Option).
- **Schalter** – GPO 1–8 (Option). Der Server meldet Ausgänge nicht zurück,
  daher „angenommener Zustand“, der letzte Wert wird nach Neustart wiederhergestellt.

## Aktionen (Dienste)

Alle Aktionen zielen auf den Media Player des jeweiligen Servers:

```yaml
# Clip laden und ab Frame 0 starten
action: doremi_dcp2000.cue_clip
target:
  entity_id: media_player.kino_1
data:
  clip: "SPOT1"        # Clip-ID, Eintrag aus der Quellenliste oder CPL-Titel
  autoplay: true

# Nächsten Clip nahtlos anhängen (Preview-Preset)
action: doremi_dcp2000.queue_clip
target: {entity_id: media_player.kino_1}
data: {clip: "TRAILER2"}

# Projektor-Kanal umschalten
action: doremi_dcp2000.projector_channel
target: {entity_id: media_player.kino_1}
data: {channel: 3}

# Beliebigen Sony-9-Pin-Befehl senden (Prüfsumme wird ergänzt), Antwort zurück
action: doremi_dcp2000.send_command
target: {entity_id: media_player.kino_1}
data: {command: "61 20 0A"}
response_variable: antwort

# Inhalte auflisten (Clips; mit KLV auch CPLs, SPLs, KDMs)
action: doremi_dcp2000.list_content
target: {entity_id: media_player.kino_1}
response_variable: inhalte
```

Weitere: `set_frame`, `step_back`, `set_gpo` (ganzes Byte), sowie mit KLV
`spl_load` (XML laden oder speichern), `spl_command`
(play/pause/eject/skip/loop), `spl_jump`, `spl_retrieve`.

Beispiel-Automation: Dowser schließen, wenn der Film zu Ende ist

```yaml
triggers:
  - trigger: state
    entity_id: sensor.kino_1_transport
    from: playing
    to: stopped
actions:
  - action: button.press
    target: {entity_id: button.kino_1_dowser_schliessen}
```

## Protokoll-Hinweise

Grundlage sind die Doremi-Dokumente *DCP-2000 API Description* v1.0/v1.5 und
*CPL & SPL API Description* (Entwurf 2006).

- **Steuerung (Port 5000):** Sony-9-Pin-Befehle in einem 12-Byte-TCP-Header
  (`s9p `). Eine persistente Verbindung, Befehle werden serialisiert, bei
  Verbindungsabbruch wird automatisch neu verbunden.
- **Clip laden:** wie im Dokument empfohlen per Auto-Modus
  (`40.41` → `48.14 <ID>` → `40.15`), nicht per veraltetem `24.31`.
- **KLV (CPL/SPL):** Das Dokument nennt keinen Port; 11730 ist der übliche
  Doremi-Port, daher einstellbar. Das Byte 6 des Pack-Keys wird beim Speichern
  automatisch ermittelt (0x04 laut Entwurf, 0x05 bei neuerer Firmware).
- **Unstimmigkeiten im Entwurf**, die die Integration abfängt: „GetCPL Response“
  ist mit `01.09` angegeben (kollidiert mit „GetSPLList Request“) – es wird
  `01.08` und `01.09` akzeptiert; bei „RetrSPL Response“ steht die Request-ID
  hinter dem XML – beide Reihenfolgen werden gelesen.
- `JumpForward/Backward` ignorieren laut Doku die Zeitangabe (fest 3 min) und
  funktionieren nur in Pause.

Debug-Log aktivieren:

```yaml
logger:
  logs:
    custom_components.doremi_dcp2000: debug
```

## Testen ohne Server

`tests/fake_dcp.py` simuliert einen DCP-2000 (Port 5000 + KLV 11730):

```bash
python3 tests/fake_dcp.py 0.0.0.0     # dann in HA die IP dieses Rechners eintragen
python3 tests/test_client.py          # Protokoll-Tests
```
