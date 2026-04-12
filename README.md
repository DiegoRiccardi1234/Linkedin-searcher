# Linkedin Searcher - Runtime Clean

Versione pulita della web app locale.

Obiettivo: cercare lavori, analizzarli con AI, gestire stato candidatura, usare chat coach.
Uso previsto: 1 persona per PC (tu e il tuo amico ognuno sul proprio computer, con i propri dati locali).

## Cosa c'e adesso nella cartella

- app/: backend FastAPI e logica applicativa
- web/: interfaccia browser
- run_webapp.py: avvio server locale
- requirements.txt: dipendenze runtime
- README.md: guida uso

Non ci sono piu file legacy, storico CSV, CV personale o test/dev script.

## Screenshot Reali Desktop (CV IT + EN + LinkedIn)

Queste immagini sono state generate con test browser automatici Playwright usando i tuoi PDF locali IT/EN. Ora includono:
- Filtri per portali multipli (LinkedIn, Indeed, Glassdoor, ZipRecruiter)
- Generatore AI di Cover Letter
- Integrazione URL profilo LinkedIn (Settings)
- Nuova UI moderna a card per i dettagli job (Match Score, Pro/Contro visivi, RAL, ecc.)

CV Italiano caricato (Settings con scelta portali e URL LinkedIn):

![Settings CV IT](screenshots/readme/settings-cv-it.png)

Dashboard con raccomandazioni (profilo IT):

![Dashboard Recommendations IT](screenshots/readme/dashboard-recommendations-it.png)

Dettaglio top job (profilo IT):

![Discover Top Job IT](screenshots/readme/discover-top-job-it.png)

CV Inglese caricato (Settings):

![Settings CV EN](screenshots/readme/settings-cv-en.png)

Dashboard con raccomandazioni (profilo EN):

![Dashboard Recommendations EN](screenshots/readme/dashboard-recommendations-en.png)

Dettaglio top job (profilo EN):

![Discover Top Job EN](screenshots/readme/discover-top-job-en.png)

Lavori consigliati mostrati nei test:

- Data & Automation Specialist (Nordic Operations)
- AI Product Engineer (NextWave Talent)
- Senior Backend Python Engineer (Alpine Data Labs)

Nota: con API key non valida il sistema mostra consiglio "Da verificare manualmente" e punteggio 0/10. Con key corretta gli score e i consigli AI vengono calcolati normalmente.

## Requisiti

- Python 3.11+
- Connessione internet
- Almeno una API key LLM, impostabile direttamente dalla pagina web
	- Cerebras
	- Groq
	- OpenAI
	- Claude (Anthropic)
	- Google Gemini

## Setup rapido (prima esecuzione)

1. Apri terminale nella cartella progetto.
2. (Opzionale ma consigliato) attiva venv.
3. Installa dipendenze:

```powershell
python -m pip install -r requirements.txt
```

4. Avvia l'app, poi inserisci le key direttamente dalla sezione web "Configurazione API".
	- Puoi anche scegliere il provider primario dalla UI.

## Avvio app

```powershell
python run_webapp.py
```

Apri nel browser:

- http://127.0.0.1:8000

## Verifica tecnica base

Controlla health endpoint:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health | ConvertTo-Json -Depth 8
```

Deve risultare:

- ok: true
- active_provider valorizzato
- active_model valorizzato

Se active_provider e "none", inserisci una key dalla UI e premi "Salva Key".

## Test funzionale passo-passo

1. Carica CV
- In UI: sezione "Carica CV"
- Formati supportati: md, txt, pdf, docx

2. Controlla profilo attivo
- In alto deve apparire il profilo nel selettore

3. Aggiungi annuncio manuale
- Compila titolo/azienda/descrizione
- Premi "Aggiungi + Analizza"
- Verifica comparsa in tabella con score e consiglio

4. Apri dettaglio rating
- Premi "Dettaglio" su una riga
- Visualizza la schermata Discover in modalita' Card Moderna con Match Score, Dettagli (RAL, Contratto, Modalita'), Pro e Contro.
- **Genera Cover Letter AI**: Clicca il pulsante dedicato nel Dettaglio per preparare al volo un messaggio InMail mirato tenendo conto di Job, CV e tuo URL LinkedIn.

5. Prova azioni stato e Link esterni
- Apri la pagina esterna (LinkedIn, Indeed) usando i pulsanti Link ("🔗") o "Apri Link Offerta"
- Candidata
- Scarta
- Riapri
- Preferito

6. Prova filtri
- Solo nuovi
- Solo preferiti
- Score minimo
- Stato
- Ricerca testo
- Max giorni

7. Prova chat coach
- Messaggio esempio: "consigliami i 3 lavori migliori da candidare"
- Verifica risposta e storico chat

8. Esegui scansione reale
- Inserisci 1-3 query nel box scansione
- Seleziona/deseleziona i portali multipli (LinkedIn, Indeed, Glassdoor, ZipRecruiter) dal blocco "Portali"
- Avvia scan
- Verifica nuovi annunci e badge "Nuovo"

9. Export CSV
- Premi "Export CSV"
- Verifica file creato in root progetto

## Aggiornare app in futuro (tu push, lui pull)

Sul PC del tuo amico:

```powershell
git pull
python -m pip install -r requirements.txt
python run_webapp.py
```

Checklist ultra rapida da mandare al tuo amico:

- [CHECKLIST_INIZIALE_2_MIN.md](CHECKLIST_INIZIALE_2_MIN.md)

## Test E2E con Playwright (consigliato per repo pubblica)

1. Installa dipendenze frontend test:

```powershell
npm install
```

2. Installa browser Playwright:

```powershell
npx playwright install chromium
```

3. Lancia i test E2E:

```powershell
npm run test:e2e
```

I test verificano caricamento UI, navigazione Dashboard/Discover/Settings, presenza configurazione multi-provider e flusso chat base.

## Dati locali e backup

Il database locale viene salvato in:

- data/searcher.db

Backup consigliato prima di update grossi:

```powershell
Copy-Item data/searcher.db data/searcher_backup.db
```

## Troubleshooting rapido

1. Modulo mancante

```powershell
python -m pip install -r requirements.txt
```

2. Porta 8000 occupata
- Chiudi il processo che usa la porta

3. Provider non attivo
- Inserisci una key nella sezione "Configura API Key" della UI
- Poi ricontrolla /api/health

4. PDF o DOCX non letto
- Verifica installazione pypdf e python-docx

## Sicurezza minima

- Non committare API key
- Non caricare CV personali sensibili in repo pubbliche
- Usa il DB solo su macchina fidata
