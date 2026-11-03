---
name: verksted-core
description: "Use when implementing, testing, reviewing or packaging Verksted — the Hermes plugin that turns folders into workspaces: the verksted tool (run/status/wait/send/cancel), the run and claim store, .verksted.yaml and toolset resolution, the Desktop part that creates and resumes sessions, Rückfragen and Freigaben, or Windows path handling."
user-invocable: false
---

# Verksted — Kern

Verksted macht einen Ordner zu einem dauerhaften Hermes-Arbeitsbereich. Der Hermes-Hauptagent
ruft das Tool `verksted`; es speichert einen Auftrag, die Desktop-Ergänzung startet dafür über
das öffentliche SDK eine **reguläre Hermes-Session** im Zielordner, und das Ergebnis kommt zur
Elternsession zurück. **Hermes behält Agentenschleife, Historie, Memory, Modellzugang und
Berechtigungsabfragen** — Verksted hält nur Zuordnungen. Vertrag ist `docs/architecture.md`;
wer davon abweichen muss, legt das dem Maintainer vor, statt es still umzubauen.

## Teile — wo sie laufen, was sie nie tun

| Teil | Läuft in | Besitzt | Tut nie |
|---|---|---|---|
| Agent-Plugin (`plugin.yaml` + Python) | Hermes-Agentprozess | Tool `verksted`, Konfig-Auflösung, Pfadprüfung, SQLite-Ablage | Sessions selbst starten; private Zugriffe auf den `GatewayRunner`; eigener Hintergrunddienst |
| Plugin-API (`dashboard/plugin_api.py`) | Backend, FastAPI unter `/api/plugins/<id>` | Claim-Vergabe, Zustandswechsel, Prüfung von Hostauth und Claim | einer UI-Sichtbarkeit als Autorisierung trauen |
| Desktop-Ergänzung (`desktop/plugin.js`) | Desktop-Renderer, app-level auf dem Desktop-Rechner | Claim nehmen, Session anlegen/fortsetzen, Ereignisse, Status, „Session öffnen“ | Freigaben beantworten; Aktionen auf eine später gewählte Verbindung umlenken |
| Hermes-Skill (`skills/verksted/SKILL.md`) | Prompt des Hermes-Hauptagenten | Arbeitsbereich erkennen, begrenzten Auftrag formulieren, nächsten Workflow-Schritt wählen | Freigaben erteilen; Ergebnisse als Chatantwort erfinden |

Agent-Plugin und Desktop-Ergänzung müssen **beide** aktiviert sein. Die normale Subagent-API
passt nicht — sie lehnt abweichende Arbeitsordner ab; deshalb normale Sessions.

## Vokabular

- **Arbeitsbereich** — registrierter Ordner, Wurzel der Konfig-Vererbung.
- **Workflow-Schritt** — `workflow/NN-name/` mit eigener `AGENTS.md` als Vertrag (Inputs,
  Aufgabe, Outputs). Der Ordnername startet nichts; der Hermes-Skill wählt den Schritt.
- **Durchlauf** — `runs/<run-id>/`: Eingaben, Zwischenstände, Ausgaben. Stabile Referenzen
  liegen getrennt unter `references/`.
- **Auftrag** — eine Einreichung an eine Kindsession, identifiziert durch `run_id`. Das Tool
  gibt einen undurchsichtigen **Handle** zurück.
- **Elternsession / Kindsession** — der aufrufende Chat / die gestartete reguläre Session.
- **Claim** — zeitlich begrenzter Bearbeitungsanspruch (Lease) eines Desktop-Fensters auf
  einen Auftrag, mit steigender **Claim-Generation**.
- **Obergrenze** — die geerbte maximale Toolset-Auswahl; Unterordner dürfen sie nur einengen.
- Zustände aus dem Entwurf: `waiting_for_gui` (neuer Auftrag, keine Desktop-Verbindung),
  „veraltet“ (laufender Auftrag, Verbindung weg — **nicht** „angehalten“), `unknown` (Ausgang
  nach Abbruch unklar), „Rückfrage“, „Freigabe erforderlich“.

## Der Startweg

`host.profileRoutes()` (vollständiger Route-Deskriptor, nicht nur der Profilname) →
`runtime.describe` → `session.create` mit `source: "tool"`, absolutem `cwd`, stabiler
`request_id`, ggf. `toolsets` → Session-ID speichern → **effektive Tools prüfen** →
`prompt.submit` mit stabiler `submission_id`. Abgleich danach über `session.resume`,
`session.events.since`, `prompt.receipt`. Jede dieser Schnittstellen vor Gebrauch am
installierten Build prüfen.

## Invarianten

1. **Speichern vor Starten.** `run_id`, aufgelöste Konfiguration, `request_id` und
   `submission_id` stehen in der Ablage, bevor ein Start angefordert wird; die IDs bleiben über
   Wiederholungen gleich.
2. **Nie blind wiederholen.** Verlorene Startbestätigung → abgleichen, nie neu starten. Nach
   einem Identitätswechsel wird ein unbestätigtes `session.create` nicht wiederholt, sondern
   abgeglichen oder `unknown`. Unklarer Ausgang → `unknown`, keine automatische Wiederholung.
3. **Angenommen ist nicht abgeschlossen.** Eine angenommene Übermittlung ist kein Erfolg. Ein
   Stop gilt erst als erledigt, wenn Hermes ihn bestätigt; ein geschlossener Tab ist kein Stop.
4. **Claims atomar.** Vergabe und Zustandswechsel in einer SQLite-Transaktion; eine höhere
   Generation sperrt Bestätigungen älterer aus; ein abgelaufener Claim erlaubt nur Abgleich.
   Zwei Fenster starten nie denselben Auftrag.
5. **Identität aus dem echten Aufrufkontext.** Elternsession und Profil kommen vom Backend,
   nie aus Tool-Argumenten. Ein Handle allein erteilt nichts: jeder Zugriff prüft die
   gespeicherte Elternbindung, die Plugin-API zusätzlich Hostauth und Claim. Die Ablage bleibt
   beim ursprünglichen Elternprofil, auch wenn die GUI ein anderes fokussiert.
6. **Zuordnung = Verbindung + Profil + Session** (bei Rückfragen zusätzlich
   Ausführungsgeneration + Request-ID). Ein gleichnamiges Profil auf einer anderen Verbindung
   ist ein anderes. „Session öffnen“ = `host.openSession(storedId, {profile, intent: "tab"})`
   auf der gespeicherten Verbindung.
7. **Tools prüfen statt vertrauen.** Ohne `toolsets` gelten die Profil-Defaults innerhalb der
   Obergrenze; eine Liste verlangt genau diese nativen Toolsets; `[]` verlangt eine wirklich
   leere Auswahl. Plugin- und MCP-Defaults können Tools hinzufügen — deshalb wird die
   **effektive** Auswahl vor dem ersten Prompt geprüft; überschreitet sie die Obergrenze oder ist
   sie nicht prüfbar, wird der Start abgelehnt.
8. **Konfiguration einfrieren.** `.verksted.yaml` wird vom Arbeitsbereich bis zum Zielordner
   gelesen; fehlende Werte erben, explizite Listen ersetzen und engen die Obergrenze nur ein.
   `profile` nur aus den im Plugin freigegebenen Profilen; keine Zugangsdaten und keine
   Backend-Adressen aus dem Projekt. Aufgelöster Stand + Fingerprint werden beim Start
   gespeichert; eine laufende Session übernimmt spätere Dateiänderungen nicht.
9. **Freigaben gehören dem Nutzer.** Das Modell-Tool hat keine Aktion für
   Berechtigungsantworten. Rückfragen und Freigaben beantwortet der Nutzer im nativen Chat der
   Kindsession; ein „genehmigt“ vom Hauptagenten ersetzt keine Nutzerfreigabe.
10. **Ergebnisse nicht unterschieben.** Ist die Elternsession inaktiv, bleibt das Ergebnis
    abrufbar und in der GUI sichtbar — es wird nie als Antwort in einen fremden Chat eingefügt.
11. **Nebenläufigkeit begrenzt.** Schreibende Läufe im selben Arbeitsordner laufen
    nacheinander; ein Start in einem Ordner, der in der Aufrufkette schon aktiv ist, wird
    abgelehnt; Parallelität und Laufdauer haben feste Grenzen.
12. **Pfade auf dem ausführenden Backend.** Kanonisieren und prüfen, wo die Session läuft:
    Laufwerksbuchstaben, Leerzeichen, Unicode, Junctions. Ein WSL-Backend hat seinen eigenen
    Pfadraum — kein Windows-Pfad wird ungeprüft weitergereicht. Kein neues Konsolenfenster,
    keine Shell-Ausgabe als Steuerprotokoll.
13. **Anweisungen selbsttragend.** Hermes lädt `AGENTS.md` selbst: in einem Git-Repo die Kette,
    außerhalb keine Elternvererbung; eine vorhandene `HERMES.md` kann die AGENTS-Kategorie
    verdrängen. Jeder eigenständig startbare Workflow-Ordner trägt genug Anweisungen oder
    verweist ausdrücklich auf die gemeinsamen Regeln.

## Grenzen von Version 1

- Toolsets, keine Allowlist einzelner eingebauter Tools. MCP-Funktionen begrenzt die
  Profilkonfiguration `tools.include`. Eine exakte Toolliste braucht eine Erweiterung der
  Hermes-Session-Policy, die Sichtbarkeit **und** Ausführung begrenzt — Prompt oder verstecktes
  Schema genügt nicht.
- Eine reduzierte Toolset-Auswahl verhindert nicht den Start profilweit konfigurierter
  MCP-Server; dafür gibt es schlanke Profile (MCP weglassen oder `enabled: false`).
- Kein Workflow-Scheduler, keine eingebetteten Antwortkarten im Elternchat, Desktop-Verbindung
  ist Pflicht.
- Die Ablage enthält nur Zuordnungen, IDs, Claim, Konfig-Fingerprint, letzten bekannten
  Zustand und Ergebnisverweis — keine Historien.
- Ordner, Profile und Toolsets strukturieren die Arbeit; sie sind keine Sandbox. Ein
  zugelassenes Terminal bleibt weitreichend.

Fertig heißt: die zutreffenden Zeilen der Abnahmetabelle in `docs/architecture.md` §7 sind
geprüft — die Windows-Zeilen auf Windows.
