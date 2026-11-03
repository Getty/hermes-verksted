**Arbeitsname: Verksted.** Ein Ordner wird zu einem dauerhaften Hermes-Arbeitsbereich; Unterordner können einzelne Prozessschritte abbilden. Ein mitgelieferter Skill veranlasst den Hauptagenten, ein kleines Plugin-Tool aufzurufen. Dieses startet oder setzt eine reguläre Hermes-Session im passenden Ordner fort, optional mit einem anderen Profil und eingeschränkter Tool-Auswahl.

**Zielumgebung:** offizielles Hermes Desktop unter Windows mit seinem vorhandenen Backend. Die nachfolgenden Verksted-Dateien und Tool-Aktionen sind ein Entwurf. Die Hermes-Schnittstellen wurden am 3. November 2026 gegen `ad2b705b4995a958b0a795e9097be661e2d1ef63` geprüft; vor Umsetzung wird der tatsächlich installierte Desktop-/Backend-Build abgeglichen.

# 1 Aufbau des Plugins

Ein **einheitliches Hermes-Pluginpaket** enthält drei Teile:

- **Skill:** erkennt passende Arbeitsbereiche, formuliert einen begrenzten Auftrag und verwendet das Verksted-Tool. Er übergibt relevante Dateien und eine kurze Aufgabenbeschreibung.

- **Python-Teil:** registriert das Tool, prüft Ordner und Konfiguration und führt eine kleine dauerhafte Zuordnung von Auftrag, Elternsession und Kindsession.

- **Desktop-Ergänzung:** übernimmt über das öffentliche SDK die nativen Session-Aufrufe und zeigt Status sowie „Session öffnen“ an.

**Hermes selbst behält Agentenschleife, Historie, Memory, Modellzugang und Berechtigungsabfragen.** Die Desktop-Ergänzung ist eine austauschbare Verbindung zum vorhandenen Gateway; dessen Kindsession läuft unabhängig vom geöffneten Tab.

Für dieses GUI-MVP ist die Desktop-Verbindung bewusst erforderlich, um neue Aufträge einzureichen oder Ergebnisse abzugleichen. Ohne sie bleibt ein neuer Auftrag `waiting_for_gui`; bei einem bereits gestarteten Auftrag wird der zuletzt bekannte Status als veraltet gekennzeichnet. Das ist keine Behauptung, dass die Ausführung angehalten wurde.

Diese Aufteilung nutzt die dokumentierten SDK-Zugänge `host.profileRoutes()`, `host.requestProfile(route, ...)`, `ctx.rest`/`ctx.socket` und `host.openSession(...)`. Sie vermeidet einen eigenen Hintergrunddienst und private Python-Zugriffe auf den GatewayRunner. [Desktop SDK](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk)

Die gewöhnliche Subagent-API passt hier nicht: Sie lehnt derzeit abweichende Arbeitsordner ab. Für unsere Arbeitsbereiche werden deshalb normale Sessions verwendet. [Subagent Lifecycle](https://hermes-agent.nousresearch.com/docs/developer-guide/subagent-lifecycle-api)

# 2 Ordner und Konfiguration

Ein Arbeitsbereich braucht zunächst nur einen vorhandenen Ordner. Konfiguration und Prozessstruktur kommen bei Bedarf hinzu:

| Pfad relativ zum Arbeitsbereich | Aufgabe |
| - | - |
| `.verksted.yaml` | Optionale Zuordnung zu Profil und Toolsets |
| `AGENTS.md` | Beschreibung des Arbeitsbereichs und seiner Arbeitsweise |
| `references/` | Wiederverwendbare Regeln und Fachinformationen |
| `workflow/10-research/AGENTS.md` | Vertrag des ersten Schritts: Inputs, Aufgabe, Outputs |
| `workflow/20-write/AGENTS.md` | Vertrag des nächsten Schritts |
| `runs/<run-id>/` | Eingaben, Zwischenergebnisse und Ausgaben dieses Durchlaufs |

Beispiel für die **von Verksted zu implementierende** Ordnerkonfiguration:

```yaml
version: 1
profile: research
toolsets: [file, web, clarify]
```

`profile` verweist auf ein vorhandenes, im Plugin freigegebenes Hermes-Profil; ohne Angabe wird das Ausgangsprofil verwendet. Die Auswahl darf keine Zugangsdaten oder beliebigen Backend-Adressen aus dem Projekt übernehmen. Ein eigener Ordner erfordert kein eigenes Profil. Ein Profil lohnt sich für abweichende Identität, Memory, Modellkonfiguration oder aktivierte Integrationen.

Verksted liest seine Konfiguration vom registrierten Arbeitsbereich bis zum Zielordner. Unterordner erben fehlende Werte; explizite Listen ersetzen die geerbte Auswahl und dürfen eine vorgegebene Obergrenze nur weiter einschränken. Profilwechsel bleiben an die freigegebenen Profile und die geerbte Tool-Obergrenze gebunden. Der aufgelöste Stand wird beim Start gespeichert; eine laufende Session übernimmt spätere Dateiänderungen nicht stillschweigend.

Hermes lädt seine Projektanweisungen weiterhin selbst. Dabei gilt: Innerhalb eines Git-Repositories wird die passende `AGENTS.md`-Kette geladen; außerhalb ist beliebige Elternordner-Vererbung nicht gegeben. Eine vorhandene `HERMES.md` kann die AGENTS-Kategorie verdrängen. Deshalb enthält jeder eigenständig startbare Workflow-Ordner ausreichende Anweisungen beziehungsweise explizite Verweise auf gemeinsame Regeln. [Prompt-Aufbau](https://github.com/NousResearch/hermes-agent/blob/ad2b705b4995a958b0a795e9097be661e2d1ef63/agent/prompt_builder.py)

# 3 Tools auswählen und schlanke Profile verwenden

Zwei Einstellungen erfüllen unterschiedliche Zwecke:

**Session-Auswahl:** Ohne `toolsets` gelten nach der Vererbung die Defaults des Zielprofils innerhalb der festgelegten Obergrenze. Eine Liste verlangt die ausgewählten nativen Hermes-Toolsets; `[]` verlangt eine tatsächlich leere Auswahl. Hermes kann durch Plugin- und MCP-Defaults zusätzliche Tools aufnehmen. Verksted muss deshalb die effektive Auswahl vor dem ersten Prompt prüfen und bei Überschreitung oder nicht prüfbarer Auswahl den Start ablehnen. Die GUI zeigt den aufgelösten Stand. Eine allgemein strikte Auswahl kann eine kleine native Policy-Erweiterung erfordern. [Session-Policy](https://github.com/NousResearch/hermes-agent/blob/ad2b705b4995a958b0a795e9097be661e2d1ef63/gateway/session_policy.py) · [Tool-Auflösung](https://github.com/NousResearch/hermes-agent/blob/ad2b705b4995a958b0a795e9097be661e2d1ef63/hermes_cli/tools_config.py)

**Start externer Dienste:** Ein schlankes Profil lässt nicht benötigte MCP-Server weg oder setzt sie auf `enabled: false`; ebenso wird seine Plugin-Aktivierung bewusst klein gehalten. Eine reduzierte Session-Toolliste allein verhindert nicht den Start profilweit konfigurierter MCP-Server. Bereits laufende gemeinsame Dienste werden durch eine neue eingeschränkte Session nicht beendet. [Gateway-Start](https://github.com/NousResearch/hermes-agent/blob/ad2b705b4995a958b0a795e9097be661e2d1ef63/gateway/run.py) · [MCP-Konfiguration](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp)

**Grenze für Version 1:** Die Session-API unterstützt Toolsets, keine beliebige Allowlist einzelner eingebauter Toolfunktionen. Einzelne MCP-Funktionen lassen sich über die native Profilkonfiguration `tools.include` begrenzen. Eine später gewünschte exakte Liste aller Toolnamen benötigt eine kleine Erweiterung der Hermes-Session-Policy, die sowohl Sichtbarkeit als auch Ausführung begrenzt. Ein Prompt oder bloßes Verstecken von Schemas erfüllt das nicht.

Ordner, Profile und Tool-Auswahl strukturieren die Arbeit; sie sind keine Betriebssystem-Sandbox. Insbesondere bleibt ein zugelassenes Terminal ein weitreichendes Werkzeug.

# 4 Start und Rückkanal

Das vorgeschlagene Tool `verksted` erhält die Aktionen `run`, `status`, `wait`, `send` und `cancel`. Es gibt einen undurchsichtigen Auftragshandle zurück. `send` führt einen Folgeauftrag in der zugeordneten Session aus; eine neue Unterhaltung wird ausdrücklich angelegt. Berechtigungsantworten gehören nicht zu diesem Modell-Tool. Jeder Zugriff prüft die gespeicherte Elternbindung; die Plugin-API prüft zusätzlich Hostauthentifizierung und Claim. Ein Handle allein erteilt keine Berechtigung. Die Zustandsablage bleibt beim ursprünglichen Elternprofil, auch wenn die GUI inzwischen ein anderes fokussiert.

Der Ablauf:

1. **Auftrag speichern:** Das Backend übernimmt Elternsession und Profil aus dem echten Aufrufkontext. Es prüft den kanonischen Zielpfad, speichert `run_id`, Konfiguration sowie Erstellungs- und Übermittlungs-ID, bevor ein Start angefordert wird.

2. **GUI-Verbindung zuordnen:** Ein Ereignis meldet nur, dass Arbeit vorliegt. Die angemeldete Desktop-Ergänzung beansprucht den Auftrag über die Plugin-API mit einem zeitlich begrenzten Bearbeitungsanspruch (Lease). Zwei Fenster dürfen nicht gleichzeitig denselben Start ausführen. Claim-Vergabe und Zustandswechsel erfolgen atomar in einer kleinen transaktionalen Ablage, beispielsweise SQLite. Steigende Claim-Generationen sperren veraltete Bestätigungen aus. Ein abgelaufener Claim erlaubt zunächst nur Abgleich.

3. **Native Session erstellen:** Die GUI verwendet den vollständigen Route-Deskriptor aus `host.profileRoutes()`. Sie prüft `runtime.describe` und ruft `session.create` mit `source: "tool"`, absolutem `cwd`, stabiler `request_id` und gegebenenfalls `toolsets` auf. Die zurückgegebene Session-ID wird gespeichert; erst nach Prüfung der effektiven Tools reicht `prompt.submit` den Auftrag mit stabiler `submission_id` ein. Die ursprüngliche authentifizierte Aufruferidentität gehört zur Zuordnung: Nach einem Identitätswechsel wird ein unbestätigter Create-Aufruf nicht wiederholt, sondern abgeglichen oder als `unknown` markiert.

4. **Ereignisse verfolgen:** Status, Rückfragen und Abschluss werden der gespeicherten Verbindung, dem Profil und der Session zugeordnet. Ereignisse aktualisieren den Zustand; nach einer Lücke wird der native Snapshot beziehungsweise die Receipt geprüft.

5. **Ergebnis zurückgeben:** Ein laufender `run`-/`wait`-Aufruf erhält den strukturierten Abschluss mit Zusammenfassung und Ausgabepfaden. `wait` wartet begrenzt auf Ereignisse. Ist die Elternsession inzwischen inaktiv, bleibt das Ergebnis abrufbar und wird in der GUI angezeigt; es wird nicht als angeblich erfolgte Antwort in einen fremden Chat eingefügt.

Hermes stellt dafür unter anderem `session.resume`, `session.events.since` und `prompt.receipt` bereit. Eine angenommene Übermittlung ist noch kein erfolgreicher Abschluss. [Native Sessions](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-local-sessions) · [Kontrollprotokoll](https://github.com/NousResearch/hermes-agent/blob/ad2b705b4995a958b0a795e9097be661e2d1ef63/gateway/session_controls.py)

Die kleine Zustandsablage enthält nur Zuordnungen, IDs, Claim, Konfigurationsfingerprint, letzten bekannten Zustand und Ergebnisverweis. Historien bleiben bei Hermes. Schreibende Läufe im selben Arbeitsordner werden im MVP serialisiert. Starts in einem bereits in der Aufrufkette aktiven Ordner werden abgelehnt; Parallelität und Laufdauer erhalten feste Grenzen.

# 5 Rückfragen und Freigaben in der GUI

Sobald die Kindsession auf Eingabe wartet, zeigt Verksted deutlich **„Rückfrage“** oder **„Freigabe erforderlich“** mit Ordner und Profil. Ein Klick öffnet genau diese Session über `host.openSession(storedId, {profile, intent: "tab"})`. Die gewählte Verbindung muss dabei ebenfalls stimmen; ein gleichnamiges Profil auf einer anderen Verbindung darf nicht verwendet werden.

Der Nutzer antwortet im normalen Hermes-Chat der Kindsession. Dadurch bleiben Freitextfragen, Auswahlfragen und Freigaben bei ihrer vorhandenen Oberfläche und ihrem ursprünglichen Request. Der Hauptagent erhält den Wartezustand und kann anschließend das Ergebnis einsammeln. Diese zusätzliche „Session öffnen“-Aktion ist die bewusst kleine Bedienlösung für Version 1; eingebettete Antwortkarten im Elternchat können später folgen. [Desktop SDK](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk)

Für die Zuordnung zählen Verbindung, Profil, Session, Ausführungsgeneration und Request-ID. Native Antworten müssen weiterhin vom Host geprüft werden. Ein freier Text wie „genehmigt“ vom Hauptagenten ersetzt keine Nutzerfreigabe. Tokens und Zugangsdaten bleiben in Hermes; das Projektmanifest erhält keine.

Nach einem GUI-Neustart werden Aufträge und native Snapshots abgeglichen. Verlorene Startbestätigungen werden nicht mit einem neuen Start beantwortet. Falls eine Ausführung nach einem Gateway-Abbruch unklar bleibt, erscheint `unknown`; sie wird nicht automatisch erneut ausgeführt. Ein Stop wird erst als abgeschlossen gemeldet, wenn Hermes ihn bestätigt. Das Schließen eines Tabs ist kein Stop. [Session-Wiederaufnahme](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-local-sessions)

# 6 Workflow und Windows

Aus dem ICM-Paper übernehmen wir drei Dinge: **ein klarer Schritt pro Ordner**, einen kurzen Vertrag aus **Inputs, Verarbeitung und Outputs** sowie die Trennung von **stabilen Referenzen und Ergebnissen je Durchlauf**. Nummerierte Ordner machen die Reihenfolge lesbar. Der Skill wählt den nächsten Schritt; der bloße Ordnername startet nichts. Zwischenstände bleiben editierbare Dateien. Ein allgemeiner Workflow-Scheduler gehört nicht zum MVP. Das Paper liefert hierfür eine Strukturidee, keinen Nachweis für einen zuverlässigen Session-Rückkanal. [ICM, Version 2](https://arxiv.org/html/2603.16021v2)

Aus `Getty/skills` übernehmen wir die bewährte Richtung der Skills [claude-headless](https://github.com/Getty/skills/blob/2543572a2cbb897a3e389a280f59d047be9f881b/claude/claude-headless/SKILL.md) und [claude-cross-session](https://github.com/Getty/skills/blob/2543572a2cbb897a3e389a280f59d047be9f881b/claude/claude-cross-session/SKILL.md): echte fortsetzbare Sessions, explizite Arbeitsverzeichnisse, strukturierte Ergebnisse, stabile technische Identitäten und getrennte Berechtigungsentscheidungen. Hermes übernimmt die Umsetzung mit seinen eigenen Schnittstellen.

Windows ist das erste Abnahmeziel. Der normale Ablauf bleibt vollständig in Desktop, ohne neue Konsole und ohne Shell-Ausgaben als Steuerprotokoll. Pfade werden auf dem ausführenden Backend validiert: Laufwerksbuchstaben, Leerzeichen, Unicode und Junctions müssen korrekt behandelt werden. Bei einem WSL-Backend gilt dessen Pfadraum; ein Windows-Pfad wird nicht ungeprüft dorthin weitergereicht. Native Windows-Unterstützung ist vorhanden; die konkrete Paket- und Desktop-Version wird vor der Umsetzung festgehalten. [Windows-Leitfaden](https://hermes-agent.nousresearch.com/docs/user-guide/windows-native)

# 7 Umsetzung in drei kleinen Schritten

**1. Durchgängiger Prototyp in der echten Windows-GUI.** Ein Tool-Aufruf erzeugt einen gespeicherten Auftrag; die Desktop-Ergänzung startet über das öffentliche SDK eine Session in einem Testordner. Eine Freitextfrage und eine verweigerte Werkzeugfreigabe müssen über den nativen Kindchat funktionieren. Die effektive Tool-Auswahl muss vor der ersten Modellausführung prüfbar sein; andernfalls wird die dafür nötige kleine Hermes-Erweiterung zum expliziten Vorlauf. Ergebnis und Wiederaufnahme erreichen dieselbe Elternzuordnung. Erst nach diesem Test werden weitere Funktionen ergänzt.

**2. Arbeitsbereiche und Profile.** Ordnerkonfiguration, native Toolsets, freigegebene Profile, Pfadprüfung, Konfigurationsfingerprint und Skill ergänzen. Zwei Beispielschritte mit unterschiedlichen Inputs/Outputs reichen als Workflow-Demonstration.

**3. Unterbrechungen und Paketierung.** Claims, verlorene Bestätigungen, GUI-Reconnect, Stop und unklare Ausgänge prüfen. Ein Paket enthält `plugin.yaml`, Python-Tool/Zustand, `skills/verksted/SKILL.md`, `desktop/plugin.js` sowie die dokumentierte `dashboard/manifest.json`-/`plugin_api.py`-Anbindung. Agent- und Desktop-Teil müssen beide aktiviert sein.

| Abnahme | Erwartung |
| - | - |
| Windows-Ordner mit Leerzeichen und Umlaut | Dateioperation erfolgt im richtigen Ordner; keine neue Konsole |
| Default, explizite Auswahl, leere und ungültige Toolsets | Eindeutige, geprüfte Auswahl; keine stille Erweiterung |
| Schlankes Profil | Nicht benötigte MCP-Server werden nicht zusätzlich gestartet |
| Zwei Sessions mit gleichzeitigen Rückfragen | Jede Antwort erreicht ausschließlich den richtigen Request |
| Verweigerte Freigabe | Vorgeschlagene Aktion wird nicht ausgeführt |
| Zwei GUI-Fenster und verlorene Startbestätigung | Kein automatischer Doppelstart |
| GUI schließen und erneut öffnen | Derselbe Auftrag wird abgeglichen; Ergebnis bleibt abrufbar |
| Gateway-Abbruch während einer Aktion | Unklares Ergebnis wird sichtbar; kein blindes Wiederholen |

**Umfang von Version 1:** lokale Arbeitsordner, optionale Profile, native Toolsets, sequentielle Prozessschritte und zuverlässige Zuordnung von Ergebnissen und Nutzerinteraktionen. Die vorhandene Hermes-Ausführung bleibt der Kern.
