# Job Finder Universale

Web app locale per cercare offerte LinkedIn/Indeed, analizzarle con AI, gestire candidatura e storico, e ricevere consigli via chat.

Stato progetto: V1.5 funzionante locale (single user con multi profili CV locali).

## 1) Cosa fa

- Upload CV in formato md, txt, pdf, docx
- Scansione offerte da LinkedIn e Indeed con python-jobspy
- Pre-filtro veloce per scartare ruoli non target
- Analisi AI con rating e consiglio candidatura
- Chat coach per consigli e supporto decisionale
- Persistenza locale su SQLite (annunci, preferenze, azioni, chat)
- Gestione stato annuncio:
  - Candidata
  - Scarta
  - Riapri
  - Preferito
- Retention annunci open non preferiti oltre N giorni
- Badge Nuovo per annunci appena trovati
- Export CSV compatibile con lo schema storico
- Migrazione CSV storico a SQLite
- Filtri avanzati in UI (status, score minimo, ricerca testo, eta max)
- Dettaglio rating completo per singolo annuncio

## 2) Funzionalita principali richieste e coperte

- App universale: si, con profilo candidato caricato da CV
- Aggiunta annuncio manuale: si (da UI)
- Chat per consigli lavoro: si
- Salvataggio preferenze e storico: si
- Rating e consiglio candidatura: si
- Selezione modello dinamica all avvio: si (provider manager + policy)
- Provider configurabile: si (ordine provider da settings)

## 3) Architettura

- Backend API: FastAPI
- UI: HTML, CSS, JS statici serviti da FastAPI
- Storage: SQLite locale
- Provider AI: Cerebras e Groq con selezione modello automatica
- Scraping: python-jobspy

Struttura progetto:

~~~text
.
|-- app
|   |-- main.py
|   |-- db.py
|   |-- config.py
|   |-- cv_ingest.py
|   |-- lifecycle.py
|   |-- models.py
|   |-- providers
|   |   |-- factory.py
|   |   |-- model_selector.py
|   |   |-- cerebras_provider.py
|   |   |-- groq_provider.py
|   |-- services
|       |-- scanner_service.py
|       |-- chat_service.py
|-- web
|   |-- index.html
|   |-- styles.css
|   |-- app.js
|-- run_webapp.py
|-- requirements.txt
|-- job_analyzer.py
|-- cv.md
|-- groq key.txt
|-- data
|   |-- searcher.db
~~~

## 4) Requisiti

- Windows 11 (testato)
- Python 3.11+
- Accesso internet
- API key almeno per un provider AI

## 5) Installazione

Dal path del progetto:

~~~powershell
C:/Users/diego/AppData/Local/Programs/Python/Python311/python.exe -m pip install -r requirements.txt
~~~

## 6) Configurazione provider AI

Opzione A (consigliata): variabili ambiente

~~~powershell
$env:CEREBRAS_API_KEY="la_tua_chiave"
$env:GROQ_API_KEY="la_tua_chiave"
~~~

Opzione B Groq da file gia presente:

- Il file groq key.txt viene letto automaticamente se GROQ_API_KEY non e impostata.

Ordine provider e parametri app in data/settings.json (opzionale). Esempio:

~~~json
{
  "llm_provider_order": ["cerebras", "groq"],
  "preferred_model": "",
  "retention_days": 15,
  "hours_old": 336,
  "max_annunci": 20,
  "delay_tra_chiamate": 1.5,
  "delay_tra_ricerche": 4.0,
  "location_default": "Torino, Italy",
  "location_remote_default": "Italy",
  "default_search_terms": [
    "Analista Funzionale Junior",
    "Junior QA Tester",
    "Junior Cybersecurity Analyst"
  ]
}
~~~

Note:
- Se non crei settings.json, vengono usati default interni.
- All avvio il sistema prova i provider in ordine e seleziona il primo disponibile.

Policy selezione modello (opzionale):

~~~json
{
  "model_selection_policy": {
    "prefer_fast": true,
    "prefer_quality": true,
    "prefer_json_reliability": true,
    "max_cost_tier": "medium",
    "weights": {
      "instruct": 30,
      "chat": 15,
      "family": 40,
      "size": 20,
      "reasoning": 6,
      "json": 12,
      "speed": 8,
      "vision_penalty": -8
    }
  }
}
~~~

## 7) Avvio applicazione

~~~powershell
C:/Users/diego/AppData/Local/Programs/Python/Python311/python.exe run_webapp.py
~~~

Apri nel browser:

- http://127.0.0.1:8000

Health API:

- http://127.0.0.1:8000/api/health

## 8) Flusso operativo consigliato

1. Carica il CV dalla sezione Upload CV
2. Avvia una scansione con termini custom o default
3. Controlla i risultati in tabella
4. Usa filtri rapidi (status, score min, testo, eta max)
5. Apri Dettaglio per vedere i campi completi del rating
6. Usa i pulsanti per segnare Candidata, Scarta, Preferito
5. Usa la chat per chiedere priorita candidatura
6. Esporta CSV quando vuoi uno snapshot

Gestione profili CV:

- Ogni upload crea un profilo
- Puoi cambiare profilo attivo dal selettore in alto
- Scan e chat usano il profilo attivo corrente

## 9) Regole lifecycle annunci

- Nuovo:
  - Ogni scansione azzera il flag Nuovo sugli annunci vecchi
  - Gli annunci scoperti in scansione corrente sono marcati Nuovo
- Stato:
  - applied: candidatura inviata
  - rejected: annuncio scartato o respinto
  - open: attivo
  - archived: archiviato da retention
- Rianalisi:
  - annunci applied, rejected, archived non vengono rianalizzati
- Retention:
  - annunci open non preferiti oltre retention_days vengono archiviati
  - preferiti restano visibili

## 10) API principali

- GET /api/health
  - stato app, provider attivo, modello attivo, preferenze
- POST /api/upload-cv
  - upload file CV (md, txt, pdf, docx)
- GET /api/profile
  - profilo attivo
- GET /api/profiles
  - lista profili candidato disponibili
- POST /api/profiles/{profile_id}/activate
  - imposta profilo attivo
- POST /api/scan
  - avvio scansione offerte
- GET /api/jobs
  - lista annunci con filtri only_new, only_favorites, status, search_text, min_score, max_age_days
- GET /api/jobs/{job_id}
  - dettaglio annuncio con analysis completa
- POST /api/jobs/manual
  - aggiunta annuncio manuale e analisi AI
- POST /api/jobs/{job_id}/action
  - azioni: applied, rejected, reopened
- POST /api/jobs/{job_id}/favorite
  - set preferito true o false
- POST /api/chat
  - messaggio chat coach
- GET /api/chat/history
  - storico chat sessione
- GET /api/preferences
  - preferenze salvate
- POST /api/preferences
  - aggiorna una preferenza
- POST /api/export/csv
  - esporta CSV con schema compatibile storico

## 11) Database SQLite

File DB:

- data/searcher.db

Tabelle principali:

- jobs
- scan_runs
- candidate_profiles
- chat_messages
- preferences
- job_actions

## 12) Migrazione CSV storico

Comando:

~~~powershell
C:/Users/diego/AppData/Local/Programs/Python/Python311/python.exe scripts/migrate_csv_to_sqlite.py --csv lavori_20260303_1626.csv --db data/searcher.db
~~~

Effetto:

- importa annunci dal CSV nello storage SQLite
- conserva punteggio/consiglio e campi analisi principali
- se "Mandata candidatura?" e valorizzato, imposta stato applied

## 13) Compatibilita con script storico

- job_analyzer.py resta nel progetto come baseline storica
- la web app esporta CSV con colonne compatibili per continuita workflow

## 14) Test automatici

Esegui i test:

~~~powershell
C:/Users/diego/AppData/Local/Programs/Python/Python311/python.exe -m pytest -q
~~~

Copertura attuale:

- policy selezione modello
- filtri DB e stato azioni
- smoke API (health, job manuale, dettaglio)

## 15) Troubleshooting

Errore import moduli mancanti:

~~~powershell
C:/Users/diego/AppData/Local/Programs/Python/Python311/python.exe -m pip install -r requirements.txt
~~~

Porta 8000 occupata:

- Chiudi il processo esistente oppure avvia uvicorn su porta differente.

Provider non disponibile:

- Controlla API key ambiente
- Verifica /api/health per provider attivo e modelli rilevati

PDF DOCX non letti:

- Verifica installazione pypdf e python-docx

## 16) Sicurezza e privacy

- Non committare groq key.txt
- Non committare API key in chiaro
- Il database locale puo contenere dati sensibili del CV
- Usa machine locale fidata

## 17) Roadmap consigliata (next)

- Alert automatici candidature prioritarie (digest giornaliero)
- Miglior parser preferenze da chat (intent avanzati)
- Dashboard analytics candidature (conversione, response rate)

## 18) Comandi rapidi

Install:

~~~powershell
C:/Users/diego/AppData/Local/Programs/Python/Python311/python.exe -m pip install -r requirements.txt
~~~

Run:

~~~powershell
C:/Users/diego/AppData/Local/Programs/Python/Python311/python.exe run_webapp.py
~~~

Health:

~~~powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health | ConvertTo-Json -Depth 8
~~~

Migrazione CSV:

~~~powershell
C:/Users/diego/AppData/Local/Programs/Python/Python311/python.exe scripts/migrate_csv_to_sqlite.py --csv lavori_20260303_1626.csv --db data/searcher.db
~~~

Test:

~~~powershell
C:/Users/diego/AppData/Local/Programs/Python/Python311/python.exe -m pytest -q
~~~
