# Changelog

## [Unreleased]

## [2.0.0] — 2026-08-16

### Changed
- **The app stops searching for somebody else.** An empty search form did not mean "I haven't said yet": it meant "use the six terms and the city the author of this app was looking for". And the resolved values were then written down as *your* last search, which the scheduler replayed and which the work-rule inference read as evidence of where you live — so one scan with an empty form was enough to convince a stranger's installation that it belonged to an AI QA engineer in Turin. What to search for now comes from you, in order of how recently you said it: what you typed, your last scan, your shortlist, the roles read off your CV. Where, the same way. When there is nothing to go on the scan stops and asks, and a scheduled scan with nothing of yours to repeat skips instead of reaching for a built-in.
- **The CV finally says where you live.** Nothing extracted a place before, so the app could only learn your city from a scan it had already picked the city for. It is read locally — Privacy Mode removes the address before the CV reaches any model, so the model cannot answer even when asked — and measured on a real CV, where the first version found nothing because the city sat in an unlabelled contact header. It never overwrites a value you set by hand.
- **The words that decide what a posting is about are yours.** Two fixed lists did that job, and both described one trade. The title gate at least used its list only as a fallback; the second one ran on every scan for every profile, so a nurse's postings shared no word with it and were dropped before anyone could read them. That second gate is gone, and the first reads your terms and your CV's skills — with nothing to go on it stands down instead of guessing a trade.
- Out of the code: the author's real CV as the model-test sample (degree mark included, in a public repository), the AI-QA example in the CV rewriting prompt, the Italy-only assumption in the salary prompt, and the Turin placeholder on the cities field.

### Added
- **The archive says which slice you are looking at, and how much it is hiding.** Five tabs with their sizes on them — to review, applied, discarded, archived, all — instead of a dropdown that said "Open". "Archived" had no entry at all before, which is how 28 auto-archived offers were in no filter. Under them, "showing X of Y".
- **The mailbox is a place in the app, not a setting.** Deciding which offer a confirmation email belongs to was the last nine lines of a 116-line card, third of eight on a seven-screen page. It has its own tab now, with a badge counting the proposals waiting — a number the API has always returned and nothing ever read.
- **Settings and Profile are four tabs each.** Profile follows the four questions it answers: who I am, what I'm looking for, what rules me out, the AI tools. Each tab opens with what is still missing from its own part, and every one of those is a button that takes you to the field that answers it.
- **The coach knows which page you are on.** It answered every question from the same place before, so "why is this offer at 3?" and "which provider should I use?" got the same career-coach framing. It can now also prepare a search, save roles, fill in a profile fact or open an offer — always as a button you press. The one action it already had used to apply itself.
- Six densities instead of two. The menu offered four and the code accepted two, so "Comfortable" and "Large" silently did nothing.
- **The app asks at the rate the provider allows, and learns what that rate is.** A scan scores four offers at a time with no pause, which on a fifteen-a-minute free tier is about ninety requests a minute against a limit of fifteen: it "worked" only because the failover caught the 429s, so every scan was spent being told no and retrying. There are now three sources for a limit, in this order — what you typed after reading your own console, what the app has **watched** on this key (a 429 and the successful calls in the minute before it are the real ceiling), and the defaults it ships with. Measurement beats the file on purpose: a free tier belongs to a project and a key, not to a provider, and the same model is 500 requests a day for one person and fifty for the next. A model whose daily allowance is gone is now removed from the failover rather than de-ranked — unlike a cooldown, a daily quota does not come back before midnight.
- **The app says which provider to use, and why.** The ⭐ on a provider card has always answered "which model, on the provider you already chose"; nobody answered the question a new user actually has, so thirteen identical cards gave no reason to pick any of them. Settings now names one, from what it has measured on your own key first and what the provider publishes second. A free tier that has closed is never suggested — it is reported as closed, and Cerebras' closes on 17 August. And where it is not verified whether a free tier trains on your prompts, it says so and links their terms, instead of implying your CV is safe: "we don't know" must never be rendered as reassurance.
- **Two providers more, both measured before shipping.** Cloudflare Workers AI — 10.000 Neurons a day free, about fifty scored offers on a 70B, and 1,2 s with clean JSON when tested through the app's own probe. And OVHcloud AI Endpoints, the only catalog here served from the EU, which matters because the prompt carries a CV; its key is optional, and storing the word `anonymous` opts into the free shared tier (slow, and the big models are usually busy). Neither was added on the strength of a list: `gpt-oss-120b` returns clean JSON on Cloudflare and truncates on OpenRouter's free tier, which is the whole argument for measuring the host and not the model name.
- **Every new verdict names the model that wrote it.** A year of failover means the archive holds scores from a dozen different models, all rendered as the same number out of ten, with no way to tell whether a low score was the offer or the scorer. Migration 029 adds the column; older rows stay blank, because attributing them from what the usage log was doing nearby would be a guess dressed as a fact.
- A weekly workflow re-reads the providers' pricing pages and opens an issue when one moves. It cannot read a rate limit — those are per project and per key — but Cerebras announced the end of its free tier days before this app was told to prefer it.

### Fixed
- **The row cap was deciding what you were allowed to see.** The list asked for 250 rows and then removed the blocked ones in Python: on a real archive "open and applicable" returned 123 rows at that cap and 174 with a bigger one. The filter is part of the query now, guarded so that a single unparseable analysis cannot take the whole list down with it, and an offer with no analysis at all — an application recovered from the mailbox — stays visible.
- The kanban asks for the whole archive rather than a page: its columns *are* the slices, and a truncated board does not show fewer cards, it shows wrong numbers. The "Open" column read 210 of 369.
- Two facts no CV can state — the driving licence and the protected-categories register — could be checked but not answered. They have fields now, along with the degree subject.
- The privacy switch was hidden until a CV existed, which is exactly backwards: whether your CV is redacted before it reaches a model is the thing to decide *before* uploading one.
- The Info tab claimed nothing leaves this computer except your prompts. It now lists every connection the app makes, and describes the app that exists rather than the one from 1.6. It also states plainly that Google's free tier uses your prompts — which contain your CV — to improve Google's products, and that human reviewers may read them.
- **The file the rate limits are read from was never in the repository.** `.gitignore` said `data/`, which also matched `app/data/`, so the defaults existed on one laptop and nowhere else; `JobFinder.spec` did not copy the folder into the frozen bundle either, which would have removed it a second time with every test still green.
- **The rate-limit panel was dead UI.** Its module was imported and never called, so the table stayed empty and the Save button did nothing.
- **The limit learner was blind.** `usage_log.error_type` held the exception class name while the learner looked for the classified cause, so a 429 arriving as an `HTTPStatusError` taught it nothing — which is most of them. The row now carries both, because two different readers match on it.
- `scripts/seed_demo.py` defaulted to the real archive: `--force` wiped a year of applications to make screenshots. It defaults to `data/demo.db`, wipes all sixteen tables rather than seven, and seeds the mailbox queue and the search terms the readiness gate needs — the demo screenshots were showing the gate instead of the app. The GIF's "reveal the matches" beat had been broken since the archive moved to its own tab: it measured a hidden node and scrolled to nothing.

## [1.8.6] — 2026-08-15

### Fixed
- **A degree is in a subject, and nothing was reading which.** The education check compares a bachelor's against a master's and says nothing about what the degree is *in*, so "Ti stai per laureare o hai una laurea in Economia" counted as satisfied by a computer-science CV and PwC's junior auditor sat at 8/10 in a shortlist built for an IT job hunt. Measured on 423 real descriptions, **245 of them name a subject** — the most stated requirement in the whole archive, and the only one nothing checked. The check now reads every degree sentence in the ad before deciding, because postings routinely name an acceptable subject in one line and a preferred one in another; a subject named as a wish is not a gate, exactly like the level; and "IT" is matched as a word and case-sensitively, because two letters are a subject in an English list and noise everywhere else. It fires on 18% of the archive, and every one of the thirteen live offers it caught was checked by hand.
- **A declared salary under your floor now moves the number, not just a badge.** It was flagged and nothing else, on the reasoning that capping would punish the rare posting honest enough to publish a figure. What that produced: a TIM internship declaring up to 9.600 € against a floor of 20.000 kept an 8/10 and led the "best for you" panel. Postings that declare nothing are still untouched — that asymmetry was the real worry and it still holds.
- Both are arguable requirements, so neither hides an offer: they lower the same ceiling years-of-experience and degree level already lower. On the real archive 39 offers gained a flag, **none lost one**, twelve scores came down, and no score was lost. Migration 027 applies it to what you already collected, and running it twice changes nothing.
- **The row cap was eating three quarters of your applications.** The offer list orders by score and takes the first page, and an application recovered from the mailbox has no score by design — there is no posting to judge — so it sorts last. On a real archive **73 of 97 applications fell past the cap** and the kanban's "Candidato" column read 23. The cap decides how many *offers* to show; it must not decide how much of your own history you get to see. The column now reads 96.
- **"No driving licence" was listed among the blockers for months, and nothing ever checked one.** The barrier was assumed to be covered by the unreachable-office rule, which it is not: a field role in your own city still needs the car. It cost a Tier-1 recommendation and an application — Siemens' *Implementation Consultant PLM*, "Valid driving license and willingness to travel within Italy". There is now a fact for it on the profile page, and it blocks only if you have said you hold none: unsaid blocks nothing, like every other fact there. Rare enough to read precisely — 11 of 423 real descriptions mention a licence, the check fires on 7, and EY's *"Nice to have: … patente B"* correctly is not one of them. Migration 028, also idempotent.

## [1.8.5] — 2026-08-15

### Fixed
- **The routine mailbox check was eating the recovery.** "Find applications" reaches back up to a year, and it inherited the sweep's rule that a message once examined is never fetched again. But the two ask different questions: the sweep asks "does this confirm an offer already in the archive", writes `no_match` when it does not, and walks on — while the recovery asks "does this record an application at all", which none of those messages had ever been asked. On a real mailbox 673 messages had been written off that way, and a 365-day recovery could not see one of them: it read 1.043 messages where 1.717 were waiting. Only a decision — applied, imported, dismissed — closes a message to the recovery now.
- **A decision taken on an already-filed message was thrown away.** Both writes were `INSERT OR IGNORE` on one row per message, so "dismissed" landing on a row the sweep had written simply vanished, and the proposal came back on the next sweep — the exact failure the review queue was built to end.
- **"None of these" is now an answer.** A proposal could only be attached to one of the offers the archive holds from that employer, or dismissed. That the archive knows the employer does not make one of its postings the one you applied for: of 53 such proposals on a real queue, the title read from the message matched an archive offer 15 times, and the other 38 were roles the archive had never collected — six unrelated Teoresi postings offered for an application to "AI Engineer". The only available answers were a false record or no record. Each proposal can now be recorded as an application of its own, and the "get the job title" button — which is what makes the choice decidable — is offered on those rows too, not only on imports.
- **Half a year of experience read as no experience at all.** The CV extractor reports a first job as `0.5` years and the profile parsed it with `int(str(...))`, so it became "unknown" — and an unknown year count switches the whole experience check off. A rewritten CV silently stopped flagging every posting that asks for three years. Fractions floor now: six months is zero whole years, not a mystery.
- **"Missing skills" offered constraints as homework.** The panel says learning these unlocks more matches, and its top five rows were things nobody can learn: "requires 3+ years of experience (your profile states 0)" at 99 occurrences, the two-year version at 40, the master's degree at 27, two unreachable cities at 16 and 15 — with Power BI, the first genuinely learnable thing on the list, at 7. The blockers are written into the same list on purpose, so each offer's own page can say why it is capped; the aggregate now leaves them out.

## [1.8.4] — 2026-08-10

### Added
- **Job Finder can now find the applications you sent before it was watching.** It believed you had sent six. Your mailbox knew of eighty-four employers over a year, and the archive had heard of twenty-seven of them — the rest happened and were invisible, because the automatic matcher only recognises postings you opened from inside the app, and most people apply from LinkedIn. "Find applications" reads the confirmation emails, proposes an offer to attach each one to when it knows the company, and offers to record the rest as applications of their own. Nothing is written until you say so.
- **Look back 90, 180 or 365 days, and count the cost first.** The window was fixed at three months. Next to it there is now a dry run that reports exactly what a real sweep would find and writes nothing at all — not even the "already looked at" marks, which matters because a message marked as looked-at is never re-read.
- **The job title, and the promise it costs, as a choice you make.** A confirmation names the employer in its subject and never the role, so a recovered application arrives without a title; the title is in the body, and not reading bodies is something this app promised. Rather than quietly redefine that, the mailbox card offers three positions: never read a body, read one only when you press the button on that message (the default), or read every confirmation. In all three the message is parsed in memory and never stored, never logged and never sent to a model.
- **Reminders now actually remind.** They were a list you had to remember to go and read. A due reminder can raise a desktop notification, opt-in and announced once, and how long an application may sit before it nudges you is finally a setting instead of a value only reachable by editing the database.
- **Four things the app could already do, and offered no way to ask for**: exporting your applications (with the CV that went with each and how it ended) rather than the whole archive, undoing a marking the mailbox made, renaming a chat session, and removing a score verdict you had given.

### Fixed
- **The review queue survives a restart.** A 90-day sweep put 110 proposals in it, the app was restarted, and every one vanished — leaving a card that read "recovery done, 0 to review", which says "I looked and there was nothing". It is now a table. The promise it is built on is unchanged: no subject, no body, no sender address. What is kept is the employer name, which is the same kind of fact the archive already holds for every offer, and a test searches every column of every table for a sentinel to keep it that way.
- **The mailbox stopped proposing work nobody could do.** Measured across 853 real messages: 116 proposals, 66 of them unanswerable. "Your application was VIEWED by X" is not a confirmation that it was sent. A subject naming a company the archive has never heard of used to fall through and settle on an offer the message had just ruled out. And with nothing at all to go on, "ambiguous" handed back every open offer, so the screen asked about whichever one sorted first. Now 50 proposals, none of them noise, and nothing true lost.
- **Years of experience written out in words were invisible.** Of 348 real postings fifteen spell the number rather than digit it, six of those state a genuine requirement, and the detector saw none of them — including a Project Manager role asking for four years sitting in the shortlist at 6/10 with no warning on it.
- **The LinkedIn profile you paste now reaches the thing that decides your scores.** It was being appended for the cover letter and the interview prep, while the scan and the chat got the bare URL — which Privacy Mode then redacted to the literal text `[URL]`.
- Two panels were written into elements that nothing ever revealed: the CV summary, including the reason an upload failed, and the provider key diagnostics.
- The applications export printed `0` where an offer has no score, which is a score nobody gave.
- Sweeping a mailbox is much faster: headers are fetched fifty at a time instead of one round-trip each — 1.554 of them in sixteen seconds.

## [1.8.3] — 2026-08-07

### Added
- **A second way to connect a Microsoft mailbox, for the app id you can actually get.** Reaching Outlook needs an app registration, and since June 2024 a personal Microsoft account cannot create one: the portal answers 401 and the button to create a directory is disabled. So Microsoft now offers two modes — Graph with the read-only `Mail.Read` permission, still the right choice whenever your registration can ask for it, and IMAP with an OAuth token, for a registration that only carries the IMAP scope. Same consent flow, same read-only behaviour in this app; the IMAP scope is simply wider than what gets used.
- **The mailbox card says where an app id comes from**, instead of leaving an empty field and a shrug: a work or school directory, an Azure account, or — if you have neither — a forwarding rule to a mailbox that does work. No app id is shipped with the app: any id in that field belongs to a registration you genuinely have.

### Fixed
- The end-to-end suite ran on port 8000, which is the port the installed app listens on. With `reuseExistingServer`, a suite launched while Job Finder was open silently tested the installed bundle instead of the working tree — and wrote its test data into the real archive.

## [1.8.2] — 2026-08-07

### Added
- **The app can read your mailbox and mark the applications you actually sent.** Applying happens on LinkedIn or on the employer's form, so nothing comes back and the archive has to be told by hand, one offer at a time, right after the moment you are least likely to do it. Connect the address you apply with and the confirmation emails are recognised and matched to the postings you opened. Gmail, Yahoo, iCloud, Libero, Virgilio, Aruba, Tiscali and corporate servers connect over IMAP with an app password; Microsoft accounts connect through Microsoft Graph, because Outlook.com stopped accepting a password over IMAP in September 2024.
- **It cannot write to your mailbox, and that is enforced by the server, not by us.** On Microsoft the consent asks for `Mail.Read`, which is read-only — the alternative IMAP scope Microsoft publishes would have granted write access this app has no use for. On IMAP the folder is opened read-only, so not even the "read" flag can change. Only message headers are ever fetched: subjects and bodies are not downloaded, not stored and not logged. `smtplib` is not imported anywhere, and a test walks the source to keep it that way.
- **No model ever sees your mail.** Recognition is deterministic: a subject that reads like a confirmation *and* a sender that is either a known hiring platform or the company you actually opened. Either signal alone was tried and rejected — the subject alone catches every job alert, the sender alone catches every LinkedIn notification. When a message could belong to two offers, nothing is changed and it goes to a review list.
- **"Test recognition" runs the rules on your real mailbox and writes nothing**, reporting what it *would* have marked. How well the rules do is unknown until they meet real mail, and the cost of being wrong is a rewritten application history.
- **A one-off look back over 90 days** finds applications sent before the mailbox was connected. Over that window the only link is the company name, so every hit is a proposal you confirm, never an automatic change. If the sweep hits its ceiling it says so.
- Every automatic marking records that it was automatic, and can be undone.

- **The app now notices when you open a posting.** Applying happens on LinkedIn or on the company's own form, so the last thing this app ever saw was the click — and it did not see that either: every link was a plain anchor, and the archive could not tell "never looked at it" from "applied three weeks ago". An opened posting is now marked as waiting for an answer, with a badge in the list and a button to say you did not apply after all. It is also the groundwork for recognising the confirmation email: knowing which four companies you opened this week is the difference between a match and a guess.

### Changed
- **A requirement you do not meet no longer makes the offer disappear.** Years of experience and degree level used to cap a posting to 3 and hide it behind "applicable only", exactly like an office you cannot reach without a car. Those two are the requirements every junior is told to apply for anyway, so they now lower a ceiling instead: 6 for the first unmet one and one less for each further one, never below 3. The offer stays in the list, says why on its badge, and can never outrank one you fully match. The other four blockers — outside the EU, a degree grade an ATS filters on, an unreachable office, a register you are not on — are not arguable and still cap and hide.
- A weighted constraint no longer skips the model. The check that decides whether to call the AI at all reads only the non-arguable blockers now, because a ceiling needs a real score to lower — otherwise the ceiling itself becomes the verdict, which is the invented number this app stopped producing in 1.7.9.

### Fixed
- **"Fortemente gradita" in the next bullet was read as being about the degree.** EY's "Junior Consultant Technology Risk" asks for a master's and scored 9/10 against a three-year degree with no blocker at all: the window that looks for a word like "gradita" took 90 characters blindly and ran 68 of them into the following requirement, which was about experience. It now stops at the end of the degree's own clause. Measured on the 238-posting archive: exactly two postings change verdict, both of them genuine master's requirements.
- The same clamp is deliberately **not** applied to the two windows next to it, and the archive says why: doing it to the experience detector destroyed 8 real requirements ("esperienza di almeno 2 anni nel ruolo", "minimum 6+ years"), because job boards routinely put the noun one line above the number, and doing it to the protected-categories check lost the single genuinely reserved posting out of the 29 that mention the law. A wide window is wrong for a word that cancels a requirement and right for a word that qualifies one.
- **Migration 021.** Neither half of the above fixes the archive on its own: an offer capped to 3 by a now-weighted constraint is released — without being handed a score back, because the number under the cap was never recorded, so it is marked unevaluated and lands in front of the re-score path — and an offer the corrected detector now sees is brought down to the ceiling, which lowers a real score rather than inventing one.

## [1.8.1] — 2026-08-06

The checks that hide offers stopped hiding the wrong ones, and a verdict can finally be revisited.

### Fixed
- **Job boards escape their markdown, and it was costing you offers.** A posting saying "almeno **1\-2 anni**" arrives with a backslash between the two numbers, which broke the range apart: the year detector could not join "1" to "anni" across it, matched the *second* number instead, and read every escaped range at its upper bound — "3\-5 anni" as five, "1\-2 anni" as two, which is exactly the threshold that blocks. The same bug worked the other way round on "5\+ years", which matched nothing at all, so sixteen genuinely senior postings sailed through unflagged while junior ones were held back.
- **Three more things that look like a seniority requirement and are not.** "Esperienza di almeno 6 mesi, maturata nel corso degli ultimi 2 anni" states a *window*, not an amount — and it was hiding an apprenticeship, the single most accessible kind of posting there is. "Una realtà con oltre 30 anni di esperienza" is the *company's* age, and it hid a posting whose title reads "JUNIOR CONSULTANT – Neolaureato/a". A salary table listing "1-2 anni di esperienza: 30000-35000" is a pay bracket. None of the three blocks anything now.
- **"Bachelor's or master's degree" no longer reads as "master's".** The master's pattern matched first and the "bachelor's or" in front of it was never read, so a posting explicitly open to a three-year degree was capped to 3 — including an AI consulting role that is otherwise the closest match in the archive. "BSc/MSc" and "laurea triennale o magistrale" read the same way now. A degree listed under "elementi con attribuzione di punteggio" is a preference expressed in the language of public-sector rankings, and is treated as one.
- **The end-of-scan audit never re-scored anything.** It collected results and then asked to re-score them — but a result is not an offer, so every single suspect raised `KeyError: 'descrizione'`, was swallowed by the surrounding handler, and the run reported "no anomaly to fix" on scans full of them. `rivalutate` had been structurally zero since the feature shipped. The audit was unit-tested as a pure function, which is exactly why nobody noticed: the wiring around it had no test, and now it does.
- **The audit stopped denouncing the app for working.** Its flat-distribution check counted offers capped by a hard block, and on a real archive most offers are correctly out of reach — so every scan reported "all the scores are the same", then sent those offers back to a model only to have them capped to 3 again. Its clone check had the same flaw for the same reason: a blocked offer's summary is a sentence the app writes ("Non candidabile: richiede 3+ anni di esperienza"), identical across 34 postings by construction. Calculated caps are out of both counts; a model actually pasting one summary over unrelated offers is still caught.
- **An empty account is no longer mistaken for a busy one.** GLM reports an exhausted balance with HTTP 429 and "Insufficient balance or no resource package. Please recharge." Read as throttling — which is what any 429 meant — that is a promise the next call might work, so every offer in a run paid another trip to the same empty account, and six ended up unevaluated carrying that sentence as their reason. A 429 whose message is about credit is now treated as an exhausted account; a genuine per-minute throttle keeps its own meaning.
- **"Re-score with AI" applied none of the checks it advertises.** The endpoint never passed the candidate's facts or the posting's work mode, so years, degree and reachable location were all structurally dead on that path: an on-site role in another city could be re-scored to 9 by the same app that hides it from the list.
- **The answer budget sat in the middle of what an answer costs.** Measured against real postings on Cerebras `gpt-oss-120b`: one offer, one JSON, 1828 to 2377 completion tokens — against a ceiling of 2100. Three replies in ten came back truncated, and a truncation wastes the whole generation, then a failover, then usually a second model. The ceiling is not a charge, so the headroom it was protecting never existed.

### Added
- **Re-scoring a slice of the archive, not one offer at a time.** A better model, a corrected rule, or a provider that was down for a whole scan applies to everything at once, and until now the only remedy was a button that appeared on one offer, and only if nobody had judged it. Settings on the archive toolbar: unscored only, applicable, or everything — with the count shown before anything is spent, live progress, and a stop button.
- **Any verdict can be asked again.** The per-offer button is no longer restricted to unscored offers.
- **Postings reserved to the protected-categories register are recognised.** A "Data analyst — Legge 68/99" scored 8 and sat third in the shortlist of someone not on that register. The check had to be built carefully rather than quickly: in a real 238-offer archive, 29 postings cite L. 68/99 and exactly ONE is reserved — the rest is equal-opportunity boilerplate ("aperta *anche* a candidati appartenenti alle categorie protette") attached to jobs anyone can apply for, including the highest-scoring offer there is. So an inclusive phrase near the mention vetoes the block, and only a posting stating the requirement *as* the requirement counts. Like every other fact here, it blocks nothing until you say whether it applies to you.
- **Migration 020.** Fixing the detectors does not unfix the archive: migration 019 only ever adds flags. This pass recomputes the three declared constraints and drops what the corrected rules no longer justify. An offer that comes out unblocked is *not* handed a score back — the number under the cap was never recorded, and inventing one is the guesswork this app stopped doing in 1.7.9. It is marked unevaluated, which puts it in front of the re-score path.

## [1.8.0] — 2026-08-04

The app now enforces what you told it about yourself, instead of asking a model to remember.

### Fixed
- **Offers you cannot apply to no longer top the list.** On a real archive of 128 postings, 40 scored 8 or more — and among them eight demanded one or two years of experience against a CV declaring none, one demanded a master's against a bachelor, and on-site roles in Rome, Savona and Reggio Emilia were recommended to someone who had written "Turin, on-site or hybrid, or full remote". The demand was sitting in the analysis' own `anni_esperienza_richiesti` field the whole time: the model extracted it and then scored as if it had not. Years, degree and location are now checked deterministically, before the model is even called, and an offer that breaks one is capped and hidden. Same archive after the change: 82 offers hidden as inapplicable, 16 left.
- **"Full Remote" now means full remote.** A posting offering "possibilità di lavorare da remoto (fino a 2 giornate su 5 settimanali)" was stored as fully remote, and so was one offering "soluzioni ibride di smart working" — the hybrid pattern missed Italian plurals, and "smart working", which in Italy means a couple of days from home, was listed as a full-remote phrase. 20 postings in the archive were mislabelled this way. The posting's own words now outrank the job board's flag in both directions.
- **Ticking "Hybrid" filters again.** The work-mode filter returned true for every row whenever hybrid was among the selections, on the grounds that job boards cannot express hybrid — so choosing it switched the whole filter off while looking applied. The filter and the stored label now read the posting with the same code.
- **The hardware probe stopped running behind your back.** Consent, once given, was taken as licence to re-probe on every page load: four child processes, two visible console windows flashing over the app, and a request to Hugging Face, every single time. The result is remembered now, and only the Refresh button looks again. The probe also no longer opens console windows at all.
- **The list stops jumping to the top.** Discarding a job rebuilt the whole table, which collapsed the page height and sent the browser back to the top of the list.
- **CV reading.** A graduation year is no longer taken from the end of a certification course when the degree line's own dates sit on the next line, and "Italiano madrelingua · Inglese B2" is read as two languages rather than one.

### Added
- **Matching data you can correct.** Years of experience, degree, grade and where you can work are shown with where each came from — the CV, you, or missing — and every one of them is editable. Until now a mis-parsed year could only be fixed by rewriting the CV text, and nothing in the scoring path read those fields anyway.
- **An end-of-scan check.** The per-offer rules and the batch clone guard each see one answer at a time, so a model answering badly across every batch produced a clean-looking scan. A final pass, with no model call, looks for a collapsed score distribution, the same summary on unrelated postings, and anything recommended while carrying a blocker — then asks again about what it found, one offer at a time, and reports how many it re-checked.
- **Four display densities instead of two.** "Compact" used to replace the responsive type scale with fixed sizes, bottoming out at 10.5px and switching off adaptation to screen width. Density is now one multiplier over the same scale, with a 12px floor.

### Changed
- **The location and work mode of an offer reach the model.** Neither was in the prompt, so no model — of any size — could tell an on-site role in another city from one round the corner. Measured across the same postings, adding them raised how well the models separate applicable offers from blocked ones by about a third.
- **The clone guard looks at more than the match axes.** Two slots could carry the same summary and the same strengths sentence while differing by one digit in the axes, and slip through.


## [1.7.10] — 2026-07-28

The scan knows when it is talking to your own machine, and the app asks before looking at it.

### Fixed
- **A scan scoring on your PC no longer wastes every call.** Choosing a local model wrote a pin, and everything worked; without that pin the app fell back to picking a model by name — and the name of the wide-context copy it had built for scoring ("jobfinder-scorer") says neither which family it belongs to nor how big it is, so the plain tag won every time. Ollama serves that one with room for 4096 tokens whatever it is asked for, and a scoring prompt is already ~2500 of CV and posting: every reply came back cut in half. Measured on a real scan: 92 calls for 6 offers judged, 43 of them truncated, and the six verdicts that survived came from whatever cloud model the failover happened to reach — not from the model you chose. Scoring on a local endpoint now takes the wide-context copy first, and a pin naming the plain tag is widened before use.
- **A local model is given a local answer budget.** The app recognised "this is running on my machine" only from the pin, so an auto-selected local scan was sent the cloud budget — which a local model, more talkative and costing nothing but time, overruns. Even the copy that had the room for it came back truncated 13 times out of 15.

### Changed
- **The app asks before inspecting your computer.** Answering "which model can this PC run" means reading the graphics card, the video memory, the RAM and the list of installed models, and — with a GPU present — asking Hugging Face what else would fit. All of that ran at every launch, before Settings had even been opened. Now the panel shows what it would read and a button to allow it; nothing is touched until you press it, and the answer is remembered from then on.

## [1.7.9] — 2026-07-28

An offer nobody read no longer gets a score.

### Changed
- **No more invented scores.** When no model could judge an offer — the provider was down or rate-limited, the answer came back unusable, or the posting carried no description worth reading — the app used to score it anyway, by counting words the ad shared with your CV. That number is indistinguishable from a real verdict once it is in the list, and it sorts among the real ones: on a real scan, seven of the eleven highest-scoring off-target offers had never been read by anything, a PAYROLL SPECIALIST at 6/10 and a DIGITAL COMMUNICATION SPECIALIST at 8/10 among them. Such an offer is now shown as **to evaluate**: no score, no advice, no radar, and a badge saying so. It stays visible and is re-scored on the next scan.
  - Existing archives are cleaned on update: offers that carry a keyword score lose it. Offers capped by a hard blocker (outside the EU, degree grade below the stated minimum) **keep** their 3/10 — that cap is computed by the app from the ad, not guessed.
  - A quality filter (minimum score) no longer returns unevaluated offers, and they are excluded from the recommendations rather than filling the gaps.
  - The score chart counts them in a bar of their own instead of dropping them silently.
- **The scan filters on the job title, not on words every ad contains.** The relevance gate read title+description and only dropped a posting sharing *nothing* with the domain vocabulary — a bar no corporate ad ever fails, since "data", "software" and "cloud" appear in all of them. It dropped nothing. On a real scan, 26 of 47 postings were off-domain from the title alone and 11 of those still scored 6 or more, all from one over-broad search term. The title now has to name a trade you practise, with exemptions for a followed company and for entry routes ("Tirocinio", "Graduate"). Every drop is logged with its title and counted in the summary: a new filter has to be auditable, or what it wrongly cut is invisible.
  - **What counts as "your trade" comes from you**, not from a list in the app: the terms you search, the skills on your CV, the roles you said you want. A fixed list would only ever work for the job the app was first written for — someone searching "infermiere pediatrico" would have every posting dropped by a gate that knows only AI and software words. Bare role words ("Specialist", "Consultant") are excluded from it, or searching "AI Specialist" would teach the gate that "PAYROLL SPECIALIST" is on topic.
  - **A description that talks about your trade throughout overrules a silent title**, so "RAI Specialist" at Accenture — a *Responsible AI* role, and one of the best matches in the archive — is kept. Measured: it names your words 27 times, a genuine frontend role 8, and every off-domain ad 4 or fewer. Short acronyms are matched case-sensitively, because "ai" is also an everyday Italian preposition.
- **"Candidatura spontanea" and talent-pool forms are no longer treated as jobs** — one scored 8/10. There is no role in them to score. Job boards reposting other employers' ads are flagged instead, since those sometimes carry a real position under someone else's name.
- **Default search terms carry two domain tokens each.** "AI Specialist" alone returned 27 of 47 postings and nearly all of the noise, because job boards match "Specialist" against payroll, sales and partnership roles. Typing a bare role word into the scan form now says so.
- **The scan summary distinguishes judged from unjudged** — "36 analysed" used to include offers nobody had read. It now reports how many were left to evaluate, why (rate limit, quota, unusable answer, no description) and what would fix it.

### Added
- **The local-model panel knows what a model actually costs.** It assumed every model was stored the same way, so a half-precision build was offered as fitting a card three times too small. VRAM is now computed from the quantisation the tag names (Q4, Q6, Q8, FP16, QAT…), each suggestion says what it will take and what it costs in answer quality, and a quantisation-aware build is marked as what it is: int4 size, almost no quality lost.
- **Suggestions from Hugging Face, filtered to your card.** The panel offered five tags written by hand — good ones, but the list ages the day a new model lands, and it could not know that the best build for a 12 GB card is a quantisation-aware 12B published on the Hub. It now also lists popular GGUF builds that fit, with the exact `ollama pull` command. Public endpoint, no account, no inference, cached for a day, and skipped in silence when unreachable.
- **The app says when a provider is failing, while it is failing.** Three unjudged offers in a row and the scan says so mid-run, with the reason and what would fix it, instead of letting you find out ten minutes later. Settings now also shows, per provider, what it has actually done over the last fortnight — how many calls, how many usable answers, which model is unreliable. It is read from the usage log: free, no inference, and it survives a restart. That record existed, but only inside the "Test models" report, so a provider failing every scoring call looked exactly like a working one.
- **"Re-evaluate with AI" on a single offer.** Until now a job could only be scored while it was being created or during a scan, so an offer left unjudged because the provider was throttled stayed that way until the same posting turned up again — which, for an expired ad, never happens. When the posting itself is the problem (almost no text), the button is replaced by an invitation to open the ad: another model call would come back just as empty.

### Fixed
- **Provider names are readable again** — in Settings each provider card showed its name one letter per line, with the "Free" badge squeezed into a circle. The badge added in 1.7.8 pushed an already-full header past its width, and the name was the part that gave way.
- **The model picker scrolls** — with a provider exposing hundreds of models the list was cut off with no way to reach the rest, and the provider column silently hid its last three entries.
- **The chat no longer reads an unjudged offer as a zero** — it received `Score: None/10` in its context, which a model interprets as the worst possible match.
- **A model running on your own PC no longer eats the daily budget** — the ceiling exists to protect a shared cloud free tier, but every call was counted, so a local scan that spends nothing and leaves the machine at no point could still exhaust it and refuse to start.
- **The daily request limit can finally be changed** — it could stop a scan outright, and it was writable neither through the API nor from any screen: the only remedy was editing the database by hand. It now sits under the usage bar. A small ceiling also no longer blocks an untouched budget (with a limit of 10, the "not worth starting" rule fired at zero requests used).

## [1.7.8] — 2026-07-27

The search stops being only about keywords, an application stops being only a column, and the scores can finally be told they are wrong.

### Added
- **Follow a company and the scan looks for it by name** — a keyword search never finds an employer that words its postings differently, however often it hires. Followed companies get their own search each scan, only that employer's postings are kept (a search for a company name also returns agencies that merely mention it), and the relevance filter steps aside: what this employer publishes is worth seeing even when it doesn't sound technical. Each entry says whether it has ever actually delivered a posting, so a dead channel can be told from a quiet one.
- **An application records what was sent** — the date, and the CV that went with it (the active profile is only knowable at the time), plus an outcome that the funnel could not express: an offer, a withdrawal, and above all "no response", which used to look exactly like an application still in flight. Both appear in the job detail and in the applications export.
- **Say whether a score is right** — thumbs up or down on any AI score, optionally with the score you would have given and why. The dashboard shows how often the AI agrees with you and by how much it is off when it doesn't, and the judged cases export as JSONL — an evaluation set, not a satisfaction survey. Judgements keep the score, title and company they were about, so they survive a re-score and outlive the posting itself.

- **Score jobs on your own PC** — the app reads the graphics card, says which model sizes it can actually sustain, lists what is already downloaded and offers the rest through Ollama, then points the scan at it in one click. Local scoring spends no daily quota, cannot be rate-limited, and sends nothing to a third party. Measured on a 12B model and a 12 GB card: ~20 seconds per offer in a batch of three, against ~13 on a good cloud day — and none of the fallbacks to a keyword estimate that a throttled free tier produces. Chat and the CV tools stay on whichever provider they were using, and if the local server is not running the scan quietly goes back to the cloud instead of estimating everything locally.

### Fixed
- **A pinned model can no longer overrule the scoring floor** — the model chosen in settings was hoisted to the top of every ranking, including the scan's, where a reasoning build is excluded on purpose because it truncates its JSON. A preference stated once, for the whole app, no longer overrides a per-task statement that this model cannot do this job. Chat and the CV tools set no floor, so there the pin still wins.
- **One "no credit" answer now retires every paid model at once** — on an account without credit each paid model had to be tried, and rejected, individually: 88 of the 191 calls in a real scan were different models discovering the same missing credit. The first such answer now stands for the provider.
- **"Free" is judged by the provider, not by the model's name** — the `:free` suffix is an OpenRouter convention, but everything on Cerebras, Groq and Google AI Studio is free tier and names nothing that way. The penalty meant for paid models was sinking exactly the models that work: measured on a real scan, the one with the best record of the day ranked below a paid model that cannot even be called.
- **A text-to-speech model can no longer be picked to write JSON** — one was, seven times, because its id says nothing about speech. Non-text builds are now excluded from scoring outright rather than merely ranked lower.
- **Reading a CV into a profile asks for a suitable model, and enough room** — it was the only AI call in the CV group that stated no policy at all, and its budget could not fit the profile it asks for. A cut-off answer there is silently replaced by the keyword-based profile, which then feeds every score.

## [1.7.7] — 2026-07-27

Scores you can trust and act on: the scan stops quietly falling back to keyword guesses, every capped score says why, and offers can be compared side by side.

### Fixed
- **Individually-scored jobs no longer fall back to a keyword guess** — the single-job scoring request asked the model for a two-dozen-field answer while allowing it barely enough room for two sentences. Every such request was cut off mid-answer, was blamed on the model, worked through every alternative model in turn and ended on the local keyword estimate — and that same path is what a batch falls back to when one of its slots comes back wrong. Both paths now ask for the room the answer needs.
- **A keyword estimate no longer freezes a job's score forever** — a job scored locally (because the AI was unreachable, cut off, or the description was too thin) counted as "already analysed" and was never looked at again. Such estimates are now shown for what they are and re-scored on the next scan; genuine AI analyses carry an explicit version, so a change to the scoring rules re-scores everything once instead of leaving old verdicts frozen. **The first scan after this update re-scores your whole archive.**
- **The salary axis is compared against your own minimum** — the posting's real pay arrived *after* the axis had been decided and was then overwritten with a flat average. It is now compared with the minimum you set (and stays "N/D" when either side is unknown).
- **A failed auto-scan no longer restarts every minute** — the scheduler only recorded successful runs, so a scan that failed was relaunched on the very next tick, forever, spending AI quota each time. A scheduled scan also kept only the first of several saved locations.
- **"Check for updates" tells the truth after you dismiss a banner** — dismissing an update made the button report "up to date" from then on, with no way to undo it.
- **The app works offline** — the match radar and every icon came from the internet, so without a connection the chart vanished and the buttons showed raw words like "delete" and "restart_alt". Both now ship inside the app.
- **Job titles with special characters can't break the table** — titles, companies and AI advice are scraped or model-written text and were inserted into the page unescaped.
- **Imported jobs are location-checked too** — a job imported from a URL never recorded its location, so the "outside the EU" check could not apply to it.
- **A cover letter that fails to generate reports an error** — the failure used to be returned as the letter text and rendered as if the AI had written it.

### Added
- **Every capped score says why** — badges on the job row, the kanban card and the detail panel: outside the EU, degree-grade requirement, task/gig work, pay below your minimum, description too short, local estimate. Translated in all five languages, with a filter to hide the offers you can't apply to.
- **Compare offers side by side** — pick up to three and see their match axes, blockers, matching and missing skills and salary in one view.
- **The match radar explains itself** — each axis gets a one-line reason drawn from data the app already has, at no extra AI cost.
- **Freshness on every offer** — "last seen N days ago", and "probably expired" past a month, so a stale archive says so.
- **Sortable job table** — by score, title, company or location.

### Changed
- **The scoring model is picked from what your models actually did** — the app reads back its own call log (valid-JSON rate, cut-off answers, latency) and de-ranks models with a bad record, instead of judging them by their name; unlike before, this memory survives a restart. Rate-limited calls are not held against a model. The "Test models" check now uses the real scoring prompt on a sample posting rather than a toy question that every model passes.
- **The AI is asked for less, in a better order** — five fields nothing ever read (or that the app computes itself) left the request, and the score is now asked for *last*, after the requirements and skills the model has just written, with an explicit scale to follow. The strengths/weaknesses fields no longer carry the developer's name.
- **The job list stops downloading every posting's full text** on each refresh (the list never showed it).
- Faster job list, chat history and timeline queries (indexes on the three hottest lookups).

### Security
- `POST /api/preferences` accepts only known preference keys. It previously accepted any key, so a single local request could switch off Privacy Mode before a CV was sent to a model.
- A rate-limited or unauthorised provider no longer gets a second request for the same answer.

## [1.7.6] — 2026-07-22

Scores you can act on: offers you legally can't take stop outranking the ones you can, and the app stops burning its daily quota on models that never answer.

### Fixed
- **A broken model no longer wins the ranking forever** — when a gateway answered with no content at all, the crash it caused wasn't recognised as that model's fault, so auto-selection kept re-picking it: one real scan spent 28 attempts on a single model that failed 19 times. Empty and unreadable replies are now classified and de-ranked like any other failure, and a model that times out is dropped immediately instead of being retried three times (measured: ~40 minutes wasted in one scan).
- **Scan scoring refuses models too small or too specialised for the job** — under a rate-limit storm every decent model was penalised and a 12-billion-parameter *vision* model ended up writing two of the top scores. Models below the quality floor, plus reasoning/vision/safety-classifier builds, are now excluded outright; if none survives, the offer gets an honest local estimate that says so.
- **Batched scoring can't copy one verdict across several jobs** — three different postings came back with byte-identical match scores (two of them 10/10). Duplicated verdicts inside a batch are detected and those jobs are re-scored one at a time.
- **Jobs you can't apply to are capped instead of recommended** — postings based outside the EU (no visa, no relocation) and postings demanding a minimum degree grade above yours were scoring up to 8 with "Apply now". Both are now hard-capped and the reason is listed under what's missing. Better still, they're detected *before* the AI call, so they no longer cost quota.
- **Work mode reflects the posting, not your search filter** — every job of a remote-flagged scan was stored as "Full Remote", including plainly on-site ones. It's now read from the posting, and left as "not specified" when nothing says.
- **The analysis always has the same shape** — the model returned three different field sets within a single scan, so the detail panel sometimes rendered an empty radar or no skills. Missing fields are filled in with neutral values.
- **Indeed searches the right country** — with several locations in one scan (e.g. Germany while the scan country was Italy) Indeed queried the wrong domain and returned nothing. The country now follows each location, and Indeed is skipped for locations no single domain can serve, with LinkedIn still running.

### Added
- **Salary expectations** — set a minimum and a target salary in your search goals: scoring weighs them, and an offer declaring less is flagged (never silently downranked). "Suggest salary with AI" proposes both figures from your CV and the offers you're already looking at, in one cached call — you review and save them yourself.
- **Task work is labelled** — platform/gig postings (pay per task, no guaranteed hours) are marked as such in the job detail, so they're not mistaken for employment.

### Changed
- The match radar hides the salary axis when nothing is known about pay, instead of drawing a confident middle score: on real data that axis was invented for 46 jobs out of 78.
- Role suggestions read from a CV now recognise AI/LLM experience (evaluation, prompting, annotation) instead of defaulting to generic developer titles.

## [1.7.5] — 2026-07-16

Smarter matching: degree requirements finally count, and Indeed coverage stops collapsing.

### Fixed
- **A job asking for a Master's/PhD can no longer score 9 for a Bachelor's CV** — the scorer now explicitly compares the posting's hard requirements (degree, minimum grade, years of experience, language level) against the CV and must make any gap visible: lower score, lower seniority match, and the missing requirement listed under "mancano". The analysis also reports the required degree (`titolo_studio_richiesto`), and the offline heuristic penalizes Master's/PhD postings too.
- **Old inflated scores heal themselves** — analyses saved before this change don't know about degree requirements, so a job that re-appears in a scan is re-scored once with the new rules instead of keeping its stale score forever. (First scan after updating may re-score more jobs than usual.)
- **Indeed no longer returns a handful of results** — jobspy applies only ONE filter server-side on Indeed: with the freshness window set, remote/job-type were silently ignored AND the date filter collapsed results in smaller markets (measured from Italy: 4 rows vs 20 for the same query). Indeed is now scraped without the server-side date filter — remote and job-type work again — and freshness is enforced locally on each posting's date, keeping postings whose date is unknown. LinkedIn behaviour is unchanged, and one site failing no longer discards the other's results.

## [1.7.4] — 2026-07-16

Stability pass: a full audit of the scan pipeline, providers and UI. Scans no longer lose scored jobs to thread races, near-empty postings can't fool the scorer, and the kanban finally archives.

### Fixed
- **Scans no longer lose scored jobs under load** — two thread races (usage logging writing outside the database lock, and the model penalty map being read and rewritten concurrently) could corrupt a write or throw away a whole batch of scored jobs mid-scan. Both paths are now properly serialized.
- **A job scored on a near-empty description is flagged, not trusted** — LinkedIn sometimes serves an 82-character marketing blurb instead of the real posting; the AI would hallucinate requirements from it. Anything under ~300 characters now takes the honest capped path (score ≤ 6, "descrizione troppo breve") and the relevance gate judges such jobs by title only.
- **An empty AI reply can no longer freeze a job at score 0 forever** — a model answering 200-with-nothing used to be persisted as a valid analysis, so the job was never re-scored. Empty replies now fail over to the next model, and a scoreless reply falls back to the heuristic.
- **Models that cut off their answers are caught on Anthropic too** — truncation detection (already live for OpenAI-compatible providers) is now wired into the Anthropic provider.
- **Rate-limit errors no longer trigger a second wasted call** — a 429/401 during JSON scoring used to fire a hidden retry at the same struggling host before failing over; transport errors now go straight to failover, and JSON wrapped in markdown fences is salvaged locally with zero extra calls.
- **A just-failed model is no longer re-proposed when the catalog is down** — the no-catalog fallback now respects model penalties (with an anti-brick escape so a single-provider setup keeps working).
- **Deleting a job cleans up after itself** — timeline entries, recruiter info and chat pins used to linger invisibly forever; deletes now remove them, and a one-off migration sweeps the orphans accumulated in existing databases.
- **Searching for `%` or `_` matches literally** instead of acting as a hidden wildcard.
- A corrupt `local_secrets.json` now logs a clear warning instead of silently unconfiguring every provider.

### Added
- **Archive from the kanban board** — the per-card status dropdown now includes "Archived" (it was also missing from the API, which rejected the action).
- Dedicated test coverage for all 23 job endpoints; dependencies pinned to the exact tested versions.

## [1.7.3] — 2026-07-14

Sharper matching: the AI reads the requirements even on long postings, and obviously off-topic jobs are dropped before they're scored.

### Fixed
- **Requirements are read even on long job descriptions** — the scorer used to only see the first ~1800 characters, so on a long posting the "Requirements" block (which comes after the intro and responsibilities) was cut off — a role asking for a Master/PhD or 5 years could still score a 9 for a junior. The description is now packed to keep the requirements section in view, so experience and qualifications actually count.
- **Off-topic jobs are dropped before scoring** — a search for niche roles ("AI Quality Analyst", "Linguistic QA Analyst") made LinkedIn return unrelated Quality Control jobs (manufacturing, food, even a spa kitchen helper). Jobs whose text shares nothing with your skills/domain are now skipped before the AI scores them, so the archive stays on-topic and less AI quota is wasted. It only drops jobs with zero overlap, and logs each one.

### Changed
- **Removing a role from the search is now permanent** — deleting a keyword chip in Job Search also removes it from your saved roles, so it no longer re-appears on the next visit.
- Role suggestions (from the CV and the coach) now prefer specific, board-searchable titles and avoid bare generic ones that match unrelated jobs.

## [1.7.2] — 2026-07-14

Correctness: the AI now actually reads LinkedIn job descriptions before scoring.

### Fixed
- **LinkedIn jobs are scored on their real description, not just the title** — LinkedIn's search only returns job cards (title/company), so the app was scoring LinkedIn jobs blind: a role requiring 3-5 years of experience could get a 9 for a junior profile because the AI never saw the requirements. The scan now fetches each LinkedIn job's full description (Indeed already included it), so experience, seniority and required skills are actually weighed. This adds ~1.5s per LinkedIn job to a scan — a fair price for scores you can trust.
- **No more `nan`/`None` leaking into scoring** — a missing field from the scraper used to become the literal text `"nan"`/`"None"` in the AI prompt; those are now cleaned to empty.

### Changed
- **A job whose description can't be fetched is flagged, not faked** — on the rare occasion LinkedIn blocks a single job's page (even after a retry), that job is marked "description unavailable — open the posting to judge" with a capped estimate from the title, so an unread job can never surface as a top "Apply now".

## [1.7.1] — 2026-07-14

Reliability: scans no longer stall on models that cut off their answers.

### Fixed
- **Scans skip models that truncate their answers** — some free models (large "reasoning" models especially) spend their token budget on hidden thinking and return a JSON reply that's cut off mid-way. That used to make a scan retry the same model over and over — or silently fill the gaps one job at a time — and crawl (a full scan could take ~20 minutes). The app now detects a cut-off reply (`finish_reason=length`), drops that model for the rest of the scan, and routes scoring to a leaner model that answers cleanly, so a scan finishes in seconds again. Works across every provider that speaks the OpenAI API (OpenRouter, Cerebras, Google, OpenAI).
- **"Test models" best pick now agrees with scoring** — the model highlighted in the free health report respects the same quality floor the scan scorer uses, so it never recommends a model too small (or too truncation-prone) for real work.

### Changed
- The scan-scoring quality floor is now ~26B (was 40B), so reliable mid-size models (e.g. gemma-class) that emit clean JSON stay eligible instead of being passed over for larger models that truncate.

## [1.7.0] — 2026-07-14

A quality pass: much faster scans, a smarter model picker, and privacy/UX fixes.

### Added
- **AI CV tools** — a dedicated panel on the Profile tab: **Review my CV** (prioritized, role-targeted advice, now rendered as clean formatted text and cached) and **Improve my CV** (an AI rewrite tuned to your target role, with your real contacts, that you can copy or save as a new active CV). Both run on a capable model.
- **Edit your CV in-app** — change your display name and the CV text directly (the text feeds AI job scoring), no re-upload needed.
- **Faster scans** — jobs are now scored in parallel instead of one at a time, so a scan finishes in seconds rather than minutes.
- **Batched scoring (fewer rate-limit failures)** — the scan now scores a few jobs per AI request instead of one each, so a scan makes far fewer calls: on a free API tier that means fewer "too many requests" errors (which otherwise drop a job to a rough keyword-only estimate) and a faster run. A batch that comes back malformed automatically falls back to per-job scoring, so quality never degrades. Tunable via `scan_batch_size` (default 3; set 1 for the old one-per-call behaviour).
- **Stop a scan for real** — the cancel button (and closing the tab) now stops the scan on the server too, so it stops using your AI quota immediately.
- **Smarter model selection** — the app learns which of your provider's models actually work: it automatically avoids models that are rate-limited, return nothing, or aren't available on your plan, and picks a fast *but capable* model for job scoring (a quality floor stops it choosing a model too small to match jobs well). On OpenRouter it also reads each model's **live health** (uptime/latency, published by OpenRouter and free to fetch — no extra AI requests) and steers scoring away from models that are down right now, so scans hit fewer errors. The **"Test models"** button in Settings now shows that free health report (uptime · latency · throughput) without spending any of your AI quota; a separate **"Confirm top models"** button optionally runs a tiny check on just the best few to verify they return valid answers.

### Fixed
- **Privacy Mode now also covers the coach chat** — your CV's email, phone and address are stripped before the chat is sent to the AI provider (the coach still knows your name).
- **Tailored résumé keeps your real contacts** — the generated résumé shows your real email/phone again instead of `[EMAIL]`/`[PHONE]` placeholders (the AI still never sees them).
- **Interview prep reads cleanly** — it's now formatted text instead of a raw data blob.
- **Profile** — the education line and the "1 year" label render correctly.
- **Job Search "remote only" filter** now refreshes the list on its own.
- A failed scan no longer wipes the "new" badges from the previous run, and two scans can no longer run at once.

### Changed
- CSV export downloads in your browser instead of writing a file into the app folder.
- Accessibility: dialogs close with Escape and trap focus; the job link has a proper label; the chat input is disabled while a reply is loading.

## [1.6.0] — 2026-07-09

A dedicated Jobs tab, a job-detail panel you can open from anywhere, application reminders, saved searches, kanban drag-and-drop, and more ways to let the AI help.

### Added
- **Jobs tab + shared detail panel** — the job archive (table + kanban) now lives in its own **Jobs** tab, and the job-detail panel is a shared side drawer you can open from the dashboard, the archive, or the coach chat (previously it only worked on the dashboard). The dashboard is now a lean overview: highlights, recommendations, reminders, analytics.
- **Application reminders & deadlines** — set a follow-up date + note on any job, and get an automatic nudge for applications that have gone quiet. A "Reminders & deadlines" card on the dashboard and a badge on the nav show what needs attention.
- **Saved searches** — save the current Job Search filters as a named preset and re-run them with one click.
- **Recruiter outreach message** — a new button on a job drafts a short, personalized message to the posting's recruiter (using the recruiter's name/role when available), in your UI language, with Privacy Mode applied.
- **Skill-gap → learning suggestions** — a "How to close them" button turns your skill gaps into concrete learning ideas (course / book / project) with a one-line why, in your language.
- **Kanban drag-and-drop** — drag a job card between Open / Applied / Interviewing / Rejected columns to change its status (with a per-card status dropdown as an accessible fallback).
- **Configurable cross-source dedup** — the same role found on LinkedIn and Indeed is now grouped into one card with an "also on" badge. A new Settings option controls the grouping (exact / by city / title+company); "by city" now matches different location spellings.
- **CV management, surfaced** — the CV history (set active / delete) moved to the top of the Profile tab, and a delete button sits next to the profile switcher on the dashboard.
- **Richer LinkedIn context** — saving your LinkedIn profile now best-effort fetches the page text, with a paste-the-text fallback when the fetch is blocked; the text feeds the AI's scoring and letters.
- **Import a job from a link** is now a visible button on the Job Search tab (previously only reachable inside the Add-a-job dialog).

### Fixed
- **Self-update no longer opens a duplicate browser tab** — after an update the existing tab reloads in place instead of a second tab opening (takes effect from the next update onward).
- Kanban now reaches all four columns on any window width (single column on narrow windows) and no longer overflows horizontally.

### Changed
- **Internal: `web/app.js` split into focused modules** (`job_list`, `job_detail`, `scan`) — the monolith dropped from ~2340 to ~1590 lines with no behavior change. Pure refactor for maintainability.

## [1.5.6] — 2026-07-08

Privacy for your CV, a CV advisor, importing jobs from a link, and a self-update that survives being reopened.

### Added
- **Privacy Mode** (on by default) — your name, email, phone and address are stripped from the CV before it's sent to any AI provider. Scoring and profile summaries never see them; cover letters and tailored resumes get your real name restored in the final text. Toggle it in Profile → CV tools & privacy.
- **CV improvement advice** — a "Review my CV" button in Profile asks the AI for prioritized, actionable suggestions. It uses your **search goals** (target sector, career goal, seniority, work mode) — a short form now in Profile — which also sharpen job scoring.
- **Import a job from a link** — the "Add a job" dialog now takes a posting URL (with a paste-the-text fallback when a site like LinkedIn blocks the fetch); the AI extracts title/company/description and scores it against your profile.
- **Unified model picker** — the coach's provider/model dropdowns are replaced by a single "Provider · Model" popover: providers on the left, models on the right with ⭐ recommended and Free/Paid groups.

### Fixed
- **Model rotation on rate limits** — when a model returns 429 the app now tries another model of the *same* provider within the request (not only another provider), and de-ranks the rate-limited one for a few minutes — so a single OpenRouter :free model going busy no longer drops chat/scoring to the degraded fallback.
- **CV tools no longer error out on a chatty model** — if the model replies in prose instead of the expected JSON, the CV review / cover letter / interview prep / resume tailoring now fall back to plain text instead of failing with a 502.
- **Self-update no longer breaks if you reopen the app mid-update** — a single-instance guard plus an update-in-progress check stop a second launch from locking `JobFinder.exe` while the updater is replacing it (the "stuck at 95%" failure). The "Update now" button also unsticks itself if a previous update was interrupted.
- **Untranslated UI strings after an update** — locale files are now cache-busted with the app version, so new translations show up immediately instead of the raw key (e.g. `chat.degradedNote`).
- **Clearer chat message when the AI is rate-limited** — the fallback now says the AI is unavailable and to add your own API key, instead of a vague "message saved".
- Fixed the wrapped "Every / Min score" labels in the auto-scan settings.

### Changed
- **Settings/Profile reorganization** — CV tools (interview prep, resume tailoring, skill-gap, CV review) and Privacy Mode now live in your Profile, next to the CV; Settings keeps API keys, scheduled scans and notifications.

## [1.5.5] — 2026-07-06

Provider-resilience hardening, native tray notifications, and a refreshed README.

### Added
- **Native desktop notifications** — the opt-in "new high-scoring jobs" alert now fires from the system-tray icon, so it works even with no browser tab open. The tray menu (Open / Quit) is also shown in your UI language.
- **Configurable GLM endpoint** — set `GLM_BASE_URL` to point the Zhipu/GLM provider at the China console (`open.bigmodel.cn`) instead of the international default.

### Fixed
- **A transient 401 no longer disables a provider for the whole session** — a key flagged invalid is automatically re-probed after a cooldown (default 10 min); if it's still bad it's simply re-flagged.
- **Auto model selection avoids a rate-limited model** — a model that keeps returning 429 is de-ranked for a few minutes so selection rotates to another, instead of hammering the same one.

### Changed
- README, demo screenshots, and the hero GIF refreshed to cover v1.5.1–v1.5.4 (quit + system tray, dark mode, dashboard job display, AI usage panel, manual add, job timeline + notes, desktop notifications).

## [1.5.4] — 2026-07-06

Four features that surface data the app already had, plus a quieter test build.

### Added
- **AI usage panel** on the dashboard — tokens and calls per provider, over Today / 7 days / 30 days / all time. The app already recorded this on every LLM call; now you can see it.
- **Add a job manually** — a "+ Add job" button in Job Search opens a short form for referrals or roles found off LinkedIn/Indeed; the job is AI-scored against your CV just like a scanned one.
- **Per-job history + notes** — the job detail panel now shows a timeline of status changes and lets you attach free-text notes.
- **Desktop notifications** (opt-in, in Settings) when the scheduled auto-scan finds new jobs above your score threshold — so you don't have to be watching the app.

### Fixed
- The bundled app no longer opens a browser tab when it is launched only for an automated health check (`JOBFINDER_NO_BROWSER`).

## [1.5.3] — 2026-07-06

Polish & fix pass: working scan filters, dark-mode fixes, dead-code cleanup, accessibility.

### Fixed
- **Scan filters now actually filter** — "On-site" work mode and multiple contract types were silently ignored (same class as the Remote-filter bug); results are filtered after scraping, keeping listings whose data the source didn't report. (Experience "Mid" stays a neutral no-narrow.)
- **The Coach's "fill the scan form" action** now takes you to Job Search — where the form actually is — instead of Settings, and the message says so.
- **Chat opened the wrong conversation on startup** — it now loads the last-active session, matching the dropdown, so replies go to the session you see.
- **Chat suggestion chips** disappear once you send a message and reappear on a fresh or emptied chat.
- **CSV export** shows an error toast instead of failing silently (e.g. when there are no jobs to export).
- **Switching the active profile** now refreshes recommendations, analytics, and skill-gap instead of leaving stale data.
- **Dark mode** — the Info tab, the post-scan summary, and the advanced-filters panel were hardcoded light and looked broken in dark theme; they now follow the theme. Brand purples, match-score colours, status pills, and destructive red were consolidated onto theme tokens so everything adapts in both light and dark.

### Changed
- **Accessibility** — visible keyboard-focus rings on all controls, `aria-label`s on icon-only buttons and the dashboard charts, press feedback, and a global uncaught-error handler.
- **Dead code removed** — unused CSS blocks and variables, an orphaned "Save draft" button, and no-op JavaScript were deleted.

## [1.5.2] — 2026-07-06

Adds a way to close the windowless app, and fixes how jobs are shown on the dashboard.

### Added
- **Quit the app** — a Quit button in the header (with a confirm) and a system-tray icon (Open / Quit). The windowless build had no terminal to close; now there's an explicit exit that stops the server cleanly.
- **Status column** in the jobs table, with a coloured pill per state (open / applied / interviewing / rejected).

### Fixed
- **Unscored jobs no longer read as "0/10"** — a job that hasn't been AI-scored yet shows "—" (not scored) instead of the worst possible score, everywhere jobs are listed.
- **Match scores are now colour-coded** (green / amber / red) in the table, kanban, recommendations, and detail panel — strong matches stand out at a glance.
- **The "Remote" filter now works** — it filtered nothing before; it now returns only jobs whose work mode is remote.
- **The jobs table has empty / loading / error states** instead of a silently blank grid.
- **The detail panel and dashboard charts are fully translated** — status labels, "location N/A", and chart labels no longer leak raw keys or mix Italian and English.
- Job links are HTML-escaped, dates are locale-formatted, and dead CSS was removed.

## [1.5.1] — 2026-07-06

Self-update reliability, a windowless build, per-request provider failover, and audit fixes.

### Fixed
- **Self-update no longer hangs at "Restart 95%"** — the bundle was built as a console app but relaunched by the updater with no console, so its first startup write hit a dead output handle and the new process died before it could serve, leaving the modal stuck forever. The app is now windowed and hardens its output streams on startup, so the relaunch always comes up. (Updating from 1.5.0 is unaffected: the new build also survives the already-installed old updater's relaunch.)
- **The update modal had no way out on failure** — if the new version never answered it sat at 95% for 10 minutes, then printed an English "refresh the page" hint that couldn't help. It now times out sooner into an explicit error state with an "open logs" action and a translated "reopen Job Finder manually" message, and reloads correctly even on fast machines that finish the swap between health checks.
- **The "reduced answer" chat indicator never rendered** — the `degraded` flag was dropped by the response model, so a canned fallback looked identical to a real LLM reply. It now reaches the UI.
- **A CV upload could freeze the whole app for minutes** — parsing/OCR and the LLM summary ran on the event loop; they now run off it, and the summary no longer retries five times around an already-retrying call (worst case dropped from ~15 min to one bounded attempt).
- **Broken secondary-text colour** — a dozen styles referenced an undefined CSS variable, rendering "muted" text at full strength; pointed at the real token.

### Added
- **No terminal window** — the app runs windowed; no console flashes on launch or auto-update.
- **Per-request provider failover** — when the active provider is rate-limited or down, chat and analysis now try the other configured providers before falling back to the offline reply; a key that returns 401 mid-session is now flagged, not just at startup.

### Changed
- **Auto model selection de-emphasises the free tier** — the `:free` bonus is now a tie-breaker instead of a large boost, so a rate-limited free model is less likely to be auto-picked over a better one.
- **Update lock TTL raised to 180s** so a slow download can't let a second updater start mid-update.

## [1.5.0] — 2026-07-03

Major release: hardened LLM provider selection, four new providers, automatic cache-busting, a rewritten CV parser, smarter chat, and job-search + settings UX upgrades.

### Added
- **Four new LLM providers** — DeepSeek, xAI (Grok), Zhipu GLM, Mistral — via a reusable `OpenAICompatibleProvider` base (a future OpenAI-compatible provider is now a tiny subclass). Ten providers total.
- **Remove-key button** per provider in Settings, so a provider you never want no longer lingers.
- **Min-salary filter** in the scan form. (Extra job sources — Glassdoor, Google, ZipRecruiter — were prototyped but pulled from the UI before release: the underlying scrapers return no results from Italy. The API still accepts them via the `sites` param.)
- **"Reduced answer" indicator** — chat now marks replies served from the offline fallback (e.g. during rate-limits) instead of passing them off as full LLM answers.

### Fixed
- **A dead provider key no longer bricks the LLM** — startup used to commit to the first provider even when its key returned 401 (a stale `CEREBRAS_API_KEY` environment variable was a common trigger); it now skips invalid providers and selects the next working one.
- **A junior CV was parsed as "Senior · 14 anni / 2016"** — experience no longer sums education/diploma date ranges, the graduation year is read from the degree line (and ignores regulation numbers like `2016/679`), and a recent graduate reads as "Junior".
- **`GET /api/scan/stream` was unguarded** — it now enforces provider-configured + rate-limit like `POST /api/scan`.
- **Multiple selected job types were silently dropped to the first** — selecting several now returns all of them.
- **Chat replied in the wrong language and forgot context** — it now replies in the language of your message and receives the recent conversation turns (not just a late summary); preference extraction no longer mis-fires on substrings ("qatar", "know").
- **Model recommendation was near-random for the new providers** — the scorer now knows the DeepSeek/xAI/GLM/Mistral (and Kimi/Command-R) model families.

### Changed
- **Cache-busting is automatic** — every app-owned asset (HTML, CSS, `chat.css`, `app.js`, and every ES module) is versioned from `__version__` at serve time, so a release no longer needs a manual `?v=` bump.
- **Settings model picker** — searchable model list on every provider, an explained ⭐ recommended marker, an "Auto (→ model)" hint showing what Auto resolves to, and a clearer key-invalid vs key-missing state.
- **Quieter logs** — OpenAI-compatible clients no longer double-retry (the SDK retry is disabled so only our own retry runs) and the `openai` logger is set to WARNING.

## [1.4.2] — 2026-06-12

Live-review bug-fix pass: restored the chat-session dropdown, killed an i18n boot race, stopped leaking provider errors into job summaries, fixed orphaned chat turns, and made the UI usable on phones.

### Fixed
- **Chat-session dropdown was empty again** (and the pinned-jobs strip silently failed) — `renderChatSessionDropdown` / `refreshPinnedStrip` called an undefined `escapeHtmlSafe`, throwing a `ReferenceError` that their `.catch()` swallowed. They now use the imported `escapeHtml`.
- **i18n boot race** — dynamically-injected markup (provider cards, chat empty-state) and the session dropdown were rendered before `initI18n()` finished, leaving English fallback text under an Italian locale and emitting `[i18n] missing translation` warnings. `applyTranslations(root)` is now exported and re-applied after dynamic injection; chat/session init runs after i18n is ready.
- **Provider errors leaked into job summaries** — the heuristic-fallback `riassunto` embedded the raw exception (e.g. `Error code: 401 - Wrong API Key`). The raw reason is now logged only; the user-facing text is generic ("IA non disponibile").
- **Orphaned chat turns** — if a turn failed after the user message was persisted, no assistant reply was saved, leaving dangling user messages. `handle_chat_message` now always persists a coherent assistant reply (or a generic error message) so history stays consistent.
- **Job detail header showed the location twice** (e.g. `Torino, PIE, IT | Score 7/10 | Torino`) — `modalita` was hardcoded to a city name for non-remote scans; it's now `In sede`, and the header de-duplicates defensively for legacy rows.

### Added
- **Responsive / mobile layout** — below 960px the top nav collapses into a hamburger menu and the Career Coach becomes an off-canvas drawer (floating button); wide tables scroll inside their wrapper and multi-column dashboards collapse to a single column. Desktop layout is unchanged.

### Changed
- **Static assets are cache-busted** (`?v=1.4.2` on `app.js` / `styles.css`) so the dashboard picks up new front-end code after a self-update without a manual hard refresh. Bump the query string on each release.

### Maintenance
- One-off `scripts/clean_dirty_data.py` (with automatic DB backup) to purge orphaned chat messages, empty sessions, and job summaries that captured a provider error before this release.

## [1.4.1] — 2026-06-12

Polish + CI fix follow-up to v1.4.0.

### Fixed
- **Dark mode** — several elements hardcoded light/yellow backgrounds (the no-API-key banner, onboarding card, post-scan score chips, skills-match chips, Info-tab cost tags, Job Search pill toggles) that looked harsh on the dark surface. They're now theme-aware; light theme is unchanged.
- **CI was red** — a floating `mypy` upgrade started flagging an optional-import guard (`requests = None`) the project's older local mypy didn't. Pinned the lint/test tools (`ruff`, `mypy`, `pytest`, `pytest-cov`) and added `types-requests` so CI and local agree, and annotated the guard.
- **Hung LLM call could exhaust the timeout thread pool** — the per-attempt timeout now uses a dedicated daemon thread per call instead of a fixed 4-worker pool, so a stuck provider can't block other calls.
- **Auto-scan run could die silently** — `run_once` now catches and logs any error and returns a status dict, so the manual "Run now" background thread never crashes unnoticed.

### Changed
- **Chat input** — now a textarea: **Enter sends**, **Shift+Enter** inserts a newline; it auto-grows as you type. The suggested-prompt chips are no longer hidden behind the input.
- **Scan dialog** — the close button now reads just "Close" (red, clearly the stop action); the minimize-to-corner button is highlighted so it's easy to find.
- **Windows bundle** — ships a `LEGGIMI.txt` / quick-start guide next to `JobFinder.exe`.

## [1.4.0] — 2026-06-11

New AI features (interview prep, resume tailoring, skill-gap, scheduled auto-scan), all toggleable, plus reliability fixes and a backend refactor.

### Added
- **Interview-prep generator** — from a job's detail panel, generate the most likely technical + behavioural interview questions for that listing, each with a CV-tailored answer hint. `POST /api/jobs/{id}/interview-prep`. Off-switch in Settings → Features.
- **Resume tailoring** — generate a version of your CV reordered and keyworded for a specific listing (truthful, ATS-friendly), with copy-to-clipboard. `POST /api/jobs/{id}/tailored-resume`. Toggleable.
- **Skill-gap analysis** — a Dashboard panel aggregating the skills your scored jobs most often flag as missing (excluding ones already on your CV), so you know what to learn. `GET /api/skill-gap`. Pure aggregation over stored analysis — no extra LLM calls. Toggleable.
- **Scheduled auto-scan** — an in-process scheduler re-runs your last search every N hours while the app is open and surfaces new jobs scoring ≥ a threshold via a Dashboard highlights banner. Configurable interval + min score, manual "Run now". `GET /api/scheduler/status`, `POST /api/scheduler/config|run-now|dismiss`. Off by default.
- **Generation infrastructure** — `app/services/generation.py` centralises profile-aware LLM generation behind prompt templates in `app/prompts/generation/`; the cover-letter endpoint now reuses it.
- **Per-feature toggles** — optional features are enabled/disabled from a new Settings → Features card, persisted in `preferences`. New i18n keys across all 5 locales (450 keys each).

### Fixed
- **DB write race** — `Database`'s lock was declared but never acquired; writes now serialize through an `@_synchronized` reentrant lock so concurrent scans / multiple tabs can't corrupt or lose updates. Reads stay lock-free (WAL).
- **Hung LLM calls could stall the SSE scan stream** — each provider attempt now runs under a wall-clock timeout (`LLM_REQUEST_TIMEOUT_SECONDS`, default 60s; Windows-safe via a thread pool), counted as a retryable error.
- **Silent exception swallowing** — two `except: pass`/`continue` sites (analytics score parsing, Cerebras model-list decode) now log at debug, honoring the project's no-silent-except policy.

### Changed
- **Backend refactor** — the 983-line `app/main.py` monolith (49 routes) was split into per-domain routers under `app/routers/` (system, providers, profile, scan, jobs, chat, preferences, scheduler) with `AppContainer` extracted to `app/container.py`. API contract unchanged.
- **E2E smoke modernised** — the Playwright smoke suite, stale since the v1.3 UI redesign, was rewritten around structural assertions (shell loads, every nav tab activates, zero console errors, provider-cards contract).

## [1.3.2] — 2026-05-06

UX polish + critical migration baseline fix.

### Fixed
- **Migration baseline skipped 005 on existing v1.2.x DBs** — `apply_migrations` used to seed the tracker at the highest known version when no `schema_version` table existed, which meant any user upgrading from v1.2.8 → v1.3.0 had migration 005 silently skipped, leaving them without the `chat_sessions` / `pinned_jobs` / `recruiters` tables and the `candidate_profiles.name` column. Result: empty chat-session dropdown, broken new/delete buttons, broken pin-to-chat flow. Fix: introduced `BASELINE_VERSION = 4` constant; baseline now seeds at the last v1.2.x version and pending migrations after it run normally. All v1.3.0 migrations are idempotent (`IF NOT EXISTS`, `INSERT OR IGNORE`, column-existence check) so re-running them on partially-applied DBs is safe.
- **Chat session dropdown empty** — `refreshChatSessions` swallowed fetch errors and left `ChatSessions.list = []`. Now always falls back to a synthetic `default` session so the dropdown is never empty, even if the backend is unreachable.
- **Post-scan summary modal didn't appear** — the show call was wrapped in a silent `try { ... } catch (_) {}` and ran *before* the scan overlay closed, so any error vanished and the modal could be covered. Now we close the scan overlay first, then show the modal, and log errors to the console so regressions surface.

### Changed
- **Chat sidebar hidden on the Info view** — the `right-rail` aside used to overlap the Info docs. `activateView('info')` now toggles `.hidden` on it. Other views keep the sidebar.
- **Chat suggestions capped at 2** — server-side default `suggest_chat_prompts(limit=2)`; frontend `loadChatPrompts` slices to 2 as a safety. Empty-state suggestions reduced from 4 to 2 keys.
- **Info view redesigned** — auto-fit grid of cards, icon per card, `<table>` for the AI providers section with cost tags (Free / Mixed / Paid). Less vertical scroll, easier to scan.
- **Job Search filters made compact** — Experience / Contract / Work-mode checkboxes converted to **pill-toggle groups** inside a collapsible `<details class="scan-advanced">` (closed by default). LinkedIn / Indeed / Remote stay as quick toggles above. Single-page form is now visually concise without losing options.

### Added
- New i18n keys: `info.providers.col.{name,cost,notes}`, `scan.filters.advanced` — translated into all 5 supported languages.

## [1.3.1] — 2026-05-06

Critical updater hotfix.

### Fixed
- **In-app updater crashed with `Failed to load Python DLL ... _internal/python311.dll`** when staging `Updater.exe` to `%TEMP%`. The v1.2.8 fix copied only `Updater.exe` to a per-PID temp dir but not the adjacent `_internal/` folder. PyInstaller's onedir bootloader loads `python311.dll` from `<exe parent>/_internal` *before* Python starts, so the staged binary crashed at launch and the install dir was left untouched (or partially overwritten by a parallel sync attempt that then hit a `PermissionError` on the locked `Updater.exe`). Fix: also `shutil.copytree` the entire `_internal/` directory next to the staged `Updater.exe`. `app/main.py:start_bundle_update`. **Users on v1.3.0 or earlier must download the v1.3.1 bundle ZIP from GitHub Releases manually** — the in-app updater on those versions still has the bug and cannot self-recover.

## [1.3.0] — 2026-05-06

Major UX & AI release: multi-chat, internship/role filters, recruiter-targeted cover letters, scan progress with ETA, post-scan summary, info tab, smarter chat output.

### Added
- **Multi-chat sessions** — switch between separate conversations with the AI Coach via dropdown next to the chat panel; create new chats and delete old ones. Auto-titles from the first user message. New tables `chat_sessions` and migration `005_v130_multichat_pin_recruiter_name.py`. Endpoints: `GET/POST/PATCH/DELETE /api/chat/sessions`.
- **Pin jobs to a chat** — open a job's detail panel and click "Pin to chat" to feed the full description (not just title+score) to the AI Coach. Pinned jobs appear as removable pills above the chat input. Endpoints: `POST/DELETE /api/chat/sessions/{id}/pin`. `chat/context.py::jobs_context` now prioritizes pinned jobs in the system prompt so the model can answer comparative questions ("which is better for me?").
- **Recruiter-targeted cover letters** — best-effort scrape of the LinkedIn job posting page extracts the poster's name/title/headline (`app/services/recruiter_scrape.py`, table `recruiters`). When available, `/api/jobs/{id}/cover-letter` opens the message with a nominal greeting and references the recruiter's role. Silently falls back to a generic letter when not exposed.
- **LinkedIn search filters** — Job Search view now exposes Experience (internship → senior), Job type (full-time, part-time, contract, temporary, internship), and Work mode (on-site, hybrid, remote) as multi-checkbox filters. `ScanRequest` carries `experience_levels`, `job_types`, `work_types` and the scanner augments search terms / forwards `job_type` to jobspy.
- **Scan progress with %, ETA and step labels** — `run_scan` emits `{status: "progress", step, current, total, percent, elapsed_ms, eta_ms}` events; UI renders a real progress bar with "Analyzing 12/80 · ETA 2m 30s".
- **Post-scan summary modal** — on completion, a modal shows totals (found / new / analyzed / skipped / archived), elapsed time and the top 3 matches with score chips.
- **Info tab** — new top-level "Info" view with sections: what is Job Finder, getting started, AI providers, scanning & filters, chat coach (multi-chat & pinning), privacy, version. Translated to all 5 languages.
- **CV name extraction → avatar initials** — the LLM CV summary now extracts the candidate's full name (with a heuristic fallback). The "D" placeholder in the top-right is replaced with the user's actual initials and tooltip.
- **Enhanced job details** — analysis JSON now includes `requisiti`, `responsabilita`, `benefit`, `skills_match {hai, mancano}`, `livello_richiesto`. The detail panel renders bullet lists, a skills match grid (have vs missing) and a recruiter card when available.

### Changed
- **Chat output sanitization** — handler now strips orphan braces / partial JSON fragments from the assistant answer (`_sanitize_chat_answer`). System prompt explicitly forbids stray `{}`, JSON fragments and filler. Mostly fixes Groq emitting random `{` characters mid-prose.
- `candidate_profiles` schema gained a `name` column (nullable). Backfilled lazily on next CV upload.


Critical updater self-overwrite fix + chat model dropdown ordering.

### Fixed
- **Update from v1.2.6 → v1.2.7 failed with `PermissionError(13) … Updater.exe`** — the updater process tried to overwrite its own running binary. Windows holds an exclusive section-object lock on a running EXE, so `shutil.copy2` is guaranteed to fail no matter how many retries. Worse, `sync_install_dir` had already overwritten most files (including `JobFinder.exe`) before reaching `Updater.exe`, leaving installs in a partially-updated state (new JobFinder + old Updater). Fix: `app/main.py` now copies `Updater.exe` to a per-PID `%TEMP%\jobfinder-updater-…` dir via `shutil.copy2` and spawns from there, so the install-dir copy is unlocked while sync runs. `scripts/updater.py` resolves the PyInstaller `_internal/` path from `--install-dir` instead of `sys.executable.parent` so imports keep working from temp. After restart, the updater spawns a detached `cmd /c timeout 5 & rmdir /s /q <tempdir>` to clean up. Defense-in-depth: `app/update_sync.py` also skips any destination that resolves to the current `sys.executable`.
- **Chat coach model dropdown was unsorted** — the `chatModelSelectorModel` in the chat panel iterated the raw API order while the Settings provider cards already sorted alphabetically (with OpenRouter Free/Paid grouping). Lifted the same logic into `_populateChatModelSelector` (`web/app.js`) so the chat dropdown matches Settings for every provider, including the recommended (⭐) model hoist.

### Added
- **`tests/unit/test_update_sync.py::test_sync_skips_current_executable`** — guards the defense-in-depth skip in `_is_current_executable`. 9 unit tests now (8 → 9).


CI hygiene.

### Fixed
- **`tests` workflow failed on `ruff format --check`** for the v1.2.6 push. The pre-commit local run only covered `ruff check` (the linter), not `ruff format --check` (the formatter). Two files (`app/main.py` and `tests/unit/test_open_logs_endpoint.py`) had stylistically minor reflow needed. Reformatted, no behavior change. The release artifact for v1.2.6 had already shipped (the `release` workflow on tag push is independent of the `tests` workflow on commit push), so this is purely a CI-green hygiene release with no user-visible effect.

## [1.2.6] — 2026-05-05

Visible app version, manual update check, log access for support.

### Added
- **Topbar version chip** — `<span class="version-chip">vX.Y.Z</span>` next to the "Job Finder" brand. Populated at boot from `/api/version`. Users always know which version they're running without opening Settings.
- **Settings → "System" card** — current version, last-check timestamp + result, "Check for updates" button, "Open logs folder" button. The check button calls `checkForUpdate({forceRefresh: true})` which forwards `?refresh=true` to `/api/version` and bypasses the 1 h `_cache` in `app/version.py` so the user gets a real GitHub round-trip on demand.
- **`POST /api/system/open-logs` endpoint** (`app/main.py`) — opens `data/logs/` in Windows Explorer via `os.startfile`. Returns 501 on non-Windows. Backed by 2 unit tests in `tests/unit/test_open_logs_endpoint.py` (157 → 159 total).
- **Update modal error state now shows "Open logs folder"** — when any step transitions to `error`, the verbose log block becomes clickable and a `→ Open logs folder for details` line is appended. One click opens `data/logs/` so the user can grab `updater.log` for support without hunting through `data\logs\` by hand. The handler is reset on each new `runUpdate()` call so the link is re-arm-able after a retry.

### Changed
- **`checkForUpdate()` is now a Promise that resolves to the version info** — was previously fire-and-forget. The Settings check button awaits it to render the result inline.

## [1.2.5] — 2026-05-05

Updater resilience against Windows Defender file scans.

### Fixed
- **`PermissionError(13)` persisted past the v1.2.1 retry budget** — three real-world update attempts each failed exactly 7 s after `replace_start` (= sum of the v1.2.1 backoff `1 s + 2 s + 4 s`). Likely cause: Windows Defender pre-scanning the freshly-extracted bundle (175 MB → ~10–20 s scan). Extended `_COPY_RETRY_DELAYS` in `app/update_sync.py` from `(1, 2, 4)` to `(1, 2, 4, 8, 16)` — five attempts spread over ~31 s, comfortably outlasting a typical AV scan window.
- **The retry-exhausted error now names the file that stayed locked** — replaced the bare `PermissionError` re-raise with one that carries `… (locked after 5 retries): D:\…\JobFinder.exe`. When updates fail again in the wild, the log identifies which file (almost always `JobFinder.exe` itself, or an OCR child) was the holdout. Previously the user only saw `Permission denied` with no file context.

### Added
- **3 s grace period after parent exit before sync starts** (`scripts/updater.py`) — `_wait_for_pid()` returns the moment the PID dies, but Windows can take a few more seconds to flush all inherited handles (uvicorn workers, Tesseract subprocess, AV pre-scan handles). The first file copy now waits 3 s after `parent_exited` instead of racing in immediately. Combined with the extended retry, the worst-case wait against AV is `3 s + 31 s = ~34 s` before the updater gives up — long enough for Defender to release locks on consumer hardware.

## [1.2.4] — 2026-05-05

Critical updater fix: restart now actually persists after Updater exits.

### Fixed
- **JobFinder.exe died seconds after restart, leaving the modal stuck at "Riavvio 95%"** — `scripts/updater.py` spawned the new JobFinder.exe with `subprocess.Popen([str(exe)])` and no `creationflags`. On Windows, `JobFinder.exe` is built with `console=True`, so the new process inherited Updater's console. When Updater returned and its cmd window closed, the JobFinder console closed with it and the just-spawned process died — port 8000 never came back up, the frontend health-poll loop spun until the 600 s timeout, and the user had no app. Fix: detach the restart with `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` (mirrors the flags `app.main.start_bundle_update` already uses to spawn `Updater.exe` from `JobFinder.exe`).

## [1.2.3] — 2026-05-05

Update banner readability + recover from stuck retry state.

### Fixed
- **Inline `<code>` segments inside the in-app release notes are now legible** — the v1.2.0 banner rendered backtick-fenced strings (`── Free ──`, `<option>`, locale keys, etc.) with a `<code>` element whose default background nearly matched the purple banner gradient, so users saw blank patches instead of text. Added explicit `background: rgba(0,0,0,0.35)`, white foreground, and a thin border so code reads on every banner state.
- **"Update now" button gets stuck after a closed modal or failed update** — when the user closed the update modal mid-run (X button) or the update errored, `localStorage["updateInProgress"]` and the backend lockfile both stayed set, so the next click was a no-op or returned HTTP 409. Modal close now clears both: it removes the localStorage flag, re-enables the button, and fires `DELETE /api/update/lock` to force-clear the backend lockfile.

### Changed
- **Backend lockfile TTL 300s → 60s** — 5 minutes was overkill for the legitimate "prevent double-spawn" case (parallel updaters fight within seconds, not minutes) and made it impossible to retry a failed update without sitting on your hands. 60s is enough to dampen accidental rapid double-clicks while keeping retry latency low.

### Added
- **`DELETE /api/update/lock` endpoint** — explicit force-clear of `data/update.lock`, called by the frontend on modal close. Safe at this point: either the updater succeeded (lockfile already gone) or it crashed (no live updater process to fight us for files).

## [1.2.2] — 2026-05-04

Settings model picker readability.

### Changed
- **OpenRouter shows all 371 models, sorted by tier then alphabetical** — replaced the v1.2.0 "Free only" toggle with a visible grouping. Models render as `── Free ──` then alphabetical free entries, then `── Paid ──` then alphabetical paid entries. Disabled `<option>` elements act as section headers. The search input still narrows by substring across both groups.
- **Other providers now sort alphabetically** — Cerebras, Groq, OpenAI, Anthropic, Google all render their model dropdowns in alpha order. The recommended ⭐ model still floats to the top regardless of name. Previously the order was whatever the provider API returned (insertion order, often arbitrary).

### Added
- Locale keys `settings.providers.freeGroup` / `paidGroup` for the OpenRouter section headers.

## [1.2.1] — 2026-05-04

Update flow reliability and UX polish. Driven by a real-world failure where v1.1.1 → v1.2.0 produced two parallel `Updater.exe` processes both racing on `JobFinder.exe` file locks (`PermissionError(13)`) and a 180 s timeout that wasn't enough for slow GitHub downloads of the 175 MB bundle.

### Added
- **Percent on the active step** — the v1.2.0 step indicator now shows the live percentage (5/10/15/50/55/70/75/90/95/100) returned by `/api/update/progress` next to the active label, e.g. *"Downloading new version · 35%"*. Step shows nothing once `done`.
- **Elapsed counter during health-poll wait** — replaced the dot-spam (`....................`) that grew while waiting for the new process to come back, with a single rewritten line `Elapsed: Xs`. No more wall of dots; users can see the wait advancing.
- **Update lockfile guard** — `POST /api/update/start` now writes `data/update.lock` (PID + target version, mtime as TTL marker) and refuses with HTTP 409 + `{code: "update_already_in_progress"}` if a second start arrives within 5 minutes of an existing one. Updater clears the lockfile on success or on any caught exception. Prevents the double-spawn race.
- **Frontend double-click guard** — the "Update now" button disables itself on click and writes `localStorage["updateInProgress"]` keyed by target version. A second click on the same version is a no-op until the page reloads or the flag is cleared. The flag is cleared on success, on timeout, and on any thrown error from `runUpdate`.
- **Retry-on-PermissionError in `sync_install_dir`** — `_copy_with_retry()` wraps `shutil.copy2` with a 3-step backoff (1 s / 2 s / 4 s) so a transient antivirus scan or a still-draining process handle no longer aborts the whole update. After the final retry, the error is propagated as before.

### Changed
- **Frontend update timeout 180 s → 600 s** — covers slow networks where a 175 MB bundle takes > 3 min to download. Elapsed counter makes the long wait observable.

### Fixed
- **Double-spawn updater race that produced `PermissionError` on `JobFinder.exe`** — root cause of the v1.1.1 → v1.2.0 update failures Diego observed (two updater PIDs spawned 35 s apart, both failed at the copy step).
- **Restart step now correctly transitions to "done" with 100%** before the page reload kicks in, instead of staying at the `active` pulsing state.

### Tooling
- 2 new tests in `tests/unit/test_update_sync.py` covering retry-on-PermissionError success and exhausted-retries propagation. Test count 155 → 157.

## [1.2.0] — 2026-05-04

UX release focused on visible release notes, a clear update flow, and a saner model picker.

### Added
- **In-app release notes** — the "Update available" banner now includes a `<details>` element rendering the release notes pulled from `/api/version` (`release_notes` field). Users see *what's new* without leaving the app. Markdown rendered via the existing `renderCoachMarkdown()` helper.
- **GitHub Release pages now show Added/Changed/Fixed bullets directly** — `release.yml` extracts the matching `[$version]` section from `CHANGELOG.md` via PowerShell regex and passes it as `body_path` to `softprops/action-gh-release@v2`. No more empty release pages with only a "Full Changelog" compare link.
- **Update progress modal with 4 step indicators** — when the user clicks "Update now", a new dialog (`#updateModal`) shows live progress through Download → Verify → Replace → Restart, driven by the new `GET /api/update/progress` endpoint that parses structured `EVENT {...}` JSON lines from `data/logs/updater.log`. Includes a hint text "The app will restart automatically and this page will reload" so users know what to expect.
- **OpenRouter search + free-only filter** — for providers exposing more than 30 models (only OpenRouter today, with 371 entries), the Settings card now shows a search input and a "Free only" checkbox above the model dropdown. The filter is applied client-side over the cached model list. Default state is "Free only" enabled, so first-time users immediately see the cheapest options.
- **Smarter Auto model picker** — `app/providers/model_selector.score_model_name()` now penalizes hard-avoid patterns (`embed`, `whisper`, `tts`, `dall-e`, `moderation`, `audio`) with `-1000`, soft-avoid (`preview`, `deprecated`, `experimental`, `alpha`) with `-50`, and rewards OpenRouter `:free` suffix with `+25`. New helper `pick_default_model()` filters out non-chat models entirely before ranking, so an embedding-only key never resolves to a chat default. 8 new unit tests in `tests/unit/test_model_selector.py`.

### Changed
- **`app/version.py`**: `release_notes` truncation raised from 500 to 2000 characters so a typical release section fits without being cut mid-sentence.
- **`scripts/updater.py`**: emits structured `EVENT {...}` JSON lines alongside the existing human-readable log, covering `started`, `parent_exited`, `download_start/done/skipped`, `verify_start/done`, `replace_start/done`, `restart_spawned`, `error`. Backwards-compatible: the human log lines remain unchanged.
- **`.github/workflows/release.yml`**: removed `generate_release_notes: true` (which only produced a "Full Changelog" auto-link) in favor of `body_path: release-notes.md` produced by the new extraction step.

### Fixed
- **Release notes invisible on GitHub Releases** — pages for v1.0.0/v1.1.0/v1.1.1 only showed a "Full Changelog: …" compare link with no content. From v1.2.0 onwards, the body is the actual `CHANGELOG.md` section.
- **Update flow appearing to hang** — previously the modal showed a single text blob ("Downloading update...") for the entire process, leaving users uncertain whether anything was happening. The new step indicator shows live state.

### Tooling
- 8 new tests covering the model picker (147 → 155 total).
- Ruff, mypy strict, format clean.

## [1.1.1] — 2026-05-04

Hotfix release focused on the bundled `Updater.exe` UX.

### Fixed
- **`Updater.exe` no longer flashes a console window** — `JobFinder.spec` now builds the updater with `console=False`. When the updater is invoked correctly by JobFinder (via `POST /api/update/start`), the user sees no transient cmd window.
- **Friendly dialog when `Updater.exe` is double-clicked** — `scripts/updater.py:main()` now detects the no-args case and shows a Windows MessageBox: *"Updater.exe is launched automatically by JobFinder. Open JobFinder.exe and click 'Update now' from the update banner."* Replaces the previous silent argparse crash that left users wondering why the cmd window vanished.

### Notes
- Update detection in v1.0.0 / v1.1.0 requires the GitHub repository to be **public** so the unauthenticated `_fetch_latest_release()` call can read `/releases/latest`. Private repos return 404 and `update_available` stays `false`.

## [1.1.0] — 2026-05-04

Quality release focused on log-spam fix, true internationalization, soft onboarding, and a token-usage tracker.

### Added
- **Token usage tracker** — every `chat` / `complete_text` / `complete_json` call now records `prompt_tokens / completion_tokens / total_tokens` per `(provider, model, endpoint)` into the new `usage_log` table. New endpoint `GET /api/usage/stats?range=today|week|month|all` returns aggregates with per-provider and per-day breakdowns. No pricing/cost — just raw counts (deferred to v1.2.0). Migration `004_usage_log.py`. Unit tests in `tests/unit/test_usage_tracker.py`.
- **OCR multi-lingua** — `app/cv_ingest._ocr_image_bytes` and `_extract_text_pdf_via_ocr` now read the language list from `JOBFINDER_OCR_LANG` env var (set by `AppContainer` from `settings.ocr_languages`). Default `eng+ita+spa+fra+deu`. Bundle ships 5 traineddata files (`scripts/build_exe.py:_REQUIRED_LANGS` extended).
- **Browser locale auto-detect** — `web/modules/i18n.js` falls back to `navigator.languages` instead of always defaulting to English. First-run users with a Spanish/French/German/Italian browser see the UI in their language immediately. Stored preference still wins over auto-detect.
- **Soft onboarding gate** — `GET /api/setup/status` returns `{ready, provider_configured, cv_loaded, first_run}`. Frontend tracks `_setupReady`; `activateView()` redirects non-Settings tabs to Settings while no provider key is configured. Banner is now non-dismissable (close button removed). Tabs get a `tab-locked` CSS class with a 🔒 badge while gated.
- **Backend 412 guard** — `/api/chat` and `/api/scan` return HTTP 412 with `{code: "no_provider_configured"}` when no provider key is configured, protecting against direct API hits even if the UI gate is bypassed.
- **Provider invalid-key flag** — new `LLMProvider.key_invalid` attribute (set on HTTP 401, cleared on key reload via `ProviderManager.invalidate_caches()`). Stops the factory from re-attempting list_models on every health poll.
- **`extract_usage()` helper** in `app/providers/base.py` — best-effort token-usage extraction across heterogeneous SDK shapes (OpenAI/Groq/Cerebras/OpenRouter `usage`, Anthropic `input_tokens`/`output_tokens`, Google).
- **Expanded CV keyword dictionary** — added 9 Spanish, 9 French, 8 German keywords (`habilidades`, `competénce`, `kenntnisse`, etc.) so the validation gate is balanced across the 5 supported locales (was Italian-heavy in v1.0).
- **Spanish/French/German CV fixtures** in `tests/unit/test_cv_ingest.py` — `validate_cv_content_accepts_spanish_cv` + French + German tests confirm cross-locale acceptance.

### Changed
- **`metadata()` is now cached for 60 seconds** (`app/providers/factory.py:_metadata_cache`). Each `/api/health` poll used to call `provider.list_models()` 6× (one per provider with a key). Now a single cached payload is returned until the TTL expires or `invalidate_caches()` is called after a key save.
- **Bundle Tesseract from 3 to 6 traineddata files** (`scripts/build_exe.py:_REQUIRED_LANGS = ("eng", "ita", "spa", "fra", "deu", "osd")`). +10-12 MB zip size (~200 MB total).
- **No-API-key banner is now non-dismissable** — close button removed; banner clears itself once `loadHealth()` sees a configured provider.
- **Version aligned to 1.1.0** across `app/version.py` + `pyproject.toml`.

### Fixed
- **Cerebras 401 spam in logs** — when a stale Cerebras key was loaded from `data/local_secrets.json`, the app emitted "Cerebras SDK list_models failed (401)" + "Cerebras HTTP list_models failed (401)" on every health poll (≈1× per second). Root cause was triple: (1) `metadata()` lacked TTL caching, (2) `is_available()` returned True regardless of key validity, (3) `list_models()` retried both SDK and HTTP paths without remembering the failure. All three are now mitigated. Single 401 line is logged on first attempt, then the provider is marked `key_invalid` and silenced until the user re-saves keys.
- **Hardcoded `lang="ita+eng"`** in `cv_ingest._ocr_image_bytes` and `_extract_text_pdf_via_ocr` — the app no longer assumes Italian for OCR.

### Tooling & Quality
- Test count: **134 → 147 passing** (13 new tests for metadata cache, key_invalid flag, usage tracker, and 4 i18n CV fixtures).
- `ruff check app/ tests/` ✅, `ruff format` ✅, `mypy --strict` ✅ on 39 source files.

### Known limits
- Pricing/cost estimation deliberately excluded from v1.1.0. Pricing tables drift fast across providers; v1.2.0 will add an opt-in cost layer.
- Welcome modal (3-step locale + key + CV picker) not shipped — the soft gate alone covers the gap. May land in v1.1.1.
- Poppler still not bundled, so scanned PDFs (vs. image CVs) remain a lossy path.

## [1.0.0] — 2026-05-04

First stable public release. Adds OCR for image CVs and scanned PDFs, ships a refreshed Profile/Job Search UX, and consolidates the standalone Windows bundle.

### Added
- **OCR pipeline for CV ingest** (`app/cv_ingest.py`): images (`.jpg/.jpeg/.png/.webp/.avif/.tiff/.bmp/.svg`) are routed through Tesseract via `pytesseract`. Scanned PDFs fall back to `pdf2image` rasterization + OCR when `pypdf` returns < 50 chars. AVIF supported via `pillow-avif-plugin`. SVG with inline `<text>` tags parsed directly; full-graphic SVG returns empty (documented limit).
- **Tesseract bundling**: `scripts/build_exe.py:_bundle_tesseract()` copies the system Tesseract install (binary + `tessdata/` ita+eng) into `dist/JobFinder/vendor/tesseract/`. `cv_ingest._resolve_tesseract_cmd()` searches override env, bundle path, system PATH, and Windows default install dirs in that order.
- **CI Tesseract install**: `.github/workflows/release.yml` now `choco install tesseract` before `python scripts/build_exe.py` so the release zip ships with OCR ready.
- **Italian/EN/ES/FR/DE years phrase parser** (`_estimate_years_from_phrases`): captures explicit `Opero da N anni`, `Lavoro da N anni`, `Over N years of experience`, `experiencia de N años`, etc. Combined with the date-range parser via `max()` so explicit phrases never lose precedence to short overlap intervals.
- **Expanded CV keyword dictionary** for content validation: now includes `abilitazion`, `qualifica`, `carriera`, `studi`, `diploma`, `laurea` plus ES/FR/DE keywords, so OCR-noisy CVs (academic, vocational) pass the keyword gate.
- **Image-format hint in CV upload**: `web/index.html` `cvFile` input `accept=` lists every supported format; `cv-dropzone-hint` reads `PDF · DOCX · MD · TXT · IMG (JPG/PNG/AVIF)`.
- **Job deletion UI** (`53e286c`): per-row trash button in the jobs list with confirmation. Cascades through `Database.delete_job()`.
- **CV deletion / Multi-CV history controls** (`0413aaa`, `f3b1c7b`): delete CVs from the Profile tab history.
- **Language extraction from CV** (`45eed86`): `_extract_languages()` parses the dedicated Languages section (5-locale headers) into chips like `Italiano (Madrelingua)`, deduped case-insensitively. Surfaces in Profile chip-list and the LLM summary.
- **Role quick prompts in chat** (`d0097de`): CV-derived prompt suggestions appear as clickable pills above the chat input.
- **Auto-save chips on Profile** (`522e012`, `e2df912`): `preferred_roles` / `skills` / `languages` chip edits PATCH the active profile inline; chat suggestions stay in sync via the same store.
- **Job Search auto-detect experience** (`36b00d9`): flat layout, no wizard stepper. Profile-derived role chips populate `wizardRoleSuggestions` directly; clicking a chip adds it as a keyword tag.
- **6 new regression tests** (`tests/unit/test_cv_ingest.py`): word-boundary skill matching, no hardcoded fallback role, Italian years phrase, English `Over N years` phrase, max(date_intervals, phrase_years), data-analyst trigger expansion. Plus 3 OCR routing tests with mocked `pytesseract`. Suite **134/134**.

### Changed
- **Heuristic skill matching now requires word boundaries** (`_keyword_present()` with `(?<![a-z0-9])kw(?![a-z0-9])`). Previously `soc` matched inside `associato`, `git` inside `logistica`, `api` inside `capi` — non-tech CVs received fake tech skills. Same boundary rule applied to `role_map` triggers, so non-tech CVs no longer default to `Junior SOC Analyst`.
- **`data analy` trigger** split into 3 explicit triggers (`data analyst`, `data analysis`, `data analytics`) so the new word-boundary rule still maps Data-related CVs to the Data Analyst role.
- **Job Search wizard removed** in favor of a flat single-card layout (`web/index.html`). Removed selectors: `#wizardAnalyzeBtn`, `.wizard-steps`. Kept selectors: `#wizardProfileSummary`, `#wizardRoleSuggestions` (now populated automatically on view-enter).
- **README**: Demo section rewritten for the flat layout (6 beats, not 7); Features lists OCR + every new behavior; Tech stack and Project structure updated; Prerequisites mention Tesseract install per OS.
- **Version aligned** across `app/version.py` (was `0.1.0`) and `pyproject.toml` (was an out-of-sync `0.3.0`) → both now `1.0.0`.

### Fixed
- **`years_experience` ignored Italian phrases** (`d2c4db4` + this release): `Opero da 7 anni` now returns `7`, not `0`. Date-range scoping to the work section (commit `d2c4db4`) avoids false positives from graduation years; explicit-phrase parser added to fill the remaining gap.
- **Heuristic CV summary false skills** on non-tech CVs (see "word boundaries" above).
- **Hardcoded `Junior SOC Analyst` fallback** that surfaced on empty templates and non-tech CVs.
- **Generic 415 error message** for unsupported uploads now lists image formats so users know they can retry.
- **Playwright specs** (`tests/e2e/readme-demo-gif.spec.js`, `tests/e2e/readme-demo-screenshots.spec.js`): removed `#wizardAnalyzeBtn` clicks; the flat Job Search now scrolls into view directly.

### Tooling & Quality
- `requirements.txt`: + `pytesseract>=0.3.10`, `pdf2image>=1.17.0`, `Pillow>=10.0.0`, `pillow-avif-plugin>=1.4.0`.
- Test count: **122 → 134 passing** (~10% growth, all new tests cover regressions or new OCR routing).
- LLM retry callback (`d2c4db4`): up to 5 attempts, progressive 3/5/7/9 s waits, optional `on_retry(attempt, wait, exc)` for UI streaming.

### Known limits
- SVG CVs without inline `<text>` (pure vector artwork) fall through OCR with empty result. Workaround: convert to PNG/JPG before upload. Adding `cairosvg` rasterization is tracked for a later release because of the GTK runtime dependency on Windows.
- OCR quality on low-DPI scans can lose keyword matches; the expanded keyword dictionary mitigates this but doesn't eliminate it. Best results: 200+ DPI scans, well-lit photos.
- `pdf2image` requires Poppler. The standalone bundle does not yet ship Poppler, so scanned PDFs (vs. image CVs) will only OCR if the user has Poppler on PATH. Tracked for v1.0.x.

## [0.1.0] — 2026-04-28

First public release. Standalone Windows bundle, self-update, multi-LLM career-coach chat, scan, kanban, analytics, AI Provider cards, Profile tab.

### Added
- **AI Provider cards** (Settings): six per-provider cards (Cerebras, Groq, OpenAI, Anthropic, Google, OpenRouter) replace the flat keys form. Each card has its own state machine (empty / configured / fetching / error / active), per-provider Save & fetch, password-visibility toggle, primary radio, ⭐-recommended model dropdown, and a refresh button. Driven by `GET /api/providers/{name}/models` with a 5-minute TTL cache.
- **Chat per-model selector**: `#chatModelSelectorModel` next to the provider override, populated live from cached provider models. Provider override list filters to providers with a key (others shown as "(no key)" disabled). `/api/chat` accepts an optional `model` field that flows through `handle_chat_message` → `provider_manager.chat(model_name=…)`.
- **"Use as default?" toast**: shown once per session after the first chat override; persists `primary_provider` + `preferred_model` via `POST /api/providers/keys` on confirm.
- **Profile tab** (`#view-profile`, new module `web/modules/profile.js`): read-only view of the AI-summarized CV (preferred_roles, skills, languages, experience, original markdown), inline chip-list edit for the three list fields, CV history accordion with **Set active** per uploaded CV.
- **`PATCH /api/profile`**, `GET /api/profiles`, `POST /api/profiles/{id}/activate` + `Database.update_candidate_profile_summary`: updates the active profile's summary; `preferred_roles` changes also sync to the `preferred_roles` preference used by the role shortlist.
- **Unit test suite** (`tests/unit/`): coverage for chat context, handler parsing, fallback, CV ingest, scanner helpers, role shortlist, migrations, memory summarizer, provider retry, rate limiter, scraper canary.
- **Schema migrations** (`app/migrations/`): lightweight `schema_version`-tracked migrations, baseline detection for pre-existing DBs. 001 init schema, 002 `chat_messages.content_type`.
- **Role shortlist service** (`app/services/roles_shortlist.py`): dedicated module + `/api/roles/shortlist` GET/POST/DELETE. Dedup case-insensitive.
- **Career Coach UX**:
  - CV-derived + localized quick prompts (`/api/chat/prompts?lang=`).
  - Markdown rendering in coach bubbles (**bold** role names, *italic* hints, `code`, `-` bullet lists).
  - Role pills: clickable suggestions that add keywords to Step 2 without launching the search.
  - `suggested_roles` field in chat JSON envelope.
  - Conversation summarizer: condenses sessions >20 messages into a summary memory row.
- **No-API-key banner**: sticky warning when zero providers are configured.
- **Radar chart** (Chart.js) in job detail: skills / seniority / remote / salary / contract axes. Backend `match_axes` in `analyze_offer`.
- **Analytics**: `top_companies` widget with horizontal bar chart.
- **Export applications**: `GET /api/applications/export?format=csv|json` with tracking-relevant columns.
- **Onboarding wizard**: 3-step welcome overlay, localized in 5 languages, surfaces only when no CV loaded.
- **Fluid layout**: clamp-based typography + grid columns; design now scales with viewport without fixed breakpoints below 960px.
- **i18n**: `coach.expand/collapse/savedToShortlist`, `onboarding.*`, `banner.*`, `analytics.topCompanies`, `offcanvas.breakdown` + axis labels, `settings.providers.*` (19 keys), `chat.modelOverride/providerOverride/modelAuto/saveAsDefault/saveAsDefaultBody`, `common.yes/no`, `profile.*` (24 keys), and `topbar.profile` across en/it/es/fr/de — 259 keys per locale, 100% parity.
- **Unit tests** for the new endpoints: `tests/unit/test_providers_models_endpoint.py` (8 tests, including TTL cache hit + `force_refresh` bypass) and `tests/unit/test_profile_endpoint.py` (9 tests, including PATCH preference sync and CV-switch via `POST /api/profiles/{id}/activate`).

### Changed
- **Provider calls** retry on 429/5xx/timeout with exponential backoff + jitter (`LLM_MAX_RETRIES`, `LLM_RETRY_BASE_SECONDS` env).
- **Rate limiter** (`app/rate_limit.py`): in-process sliding window on `/api/chat` (20/min), `/api/scan` (5/min), `/api/upload-cv` (10/min). Toggle with `ENABLE_RATE_LIMIT`.
- **Scraper pacing**: random 0.8–2.4s sleep between terms; `canary_warning` SSE event when a common keyword returns zero results.
- **Frontend**: `app.js` entry is now an ES module; shared helpers extracted to `web/modules/helpers.js`, `shortlist.js`, `theme.js`. Chat styles moved to `web/styles/chat.css`.
- **Chat JSON envelope**: clarified formatting rules (markdown markers) and documented `suggested_roles` shape.
- **Prompts**: `advising.txt` / `onboarding.txt` include a "Role exploration" section guiding CV-aware pivots.
- **E2E**: `chat-role-guidance` and `live-cv-chat-search` now skip by default; opt in with `RUN_LIVE_LLM=1`. New `chat-live-smoke.spec.js` + `live-smoke.yml` manual workflow.

### Removed
- Chat expand/collapse toggle (layout is now fully fluid).

### Tooling & Quality
- **Toolchain**: `pyproject.toml` consolidates ruff, mypy strict, pytest, and coverage config. `.pre-commit-config.yaml` adds whitespace, ruff (lint + format), and mypy hooks; `pytest.ini` removed.
- **Mypy strict**: full pass on `app/`. New `Callable[[], _RetryT] -> _RetryT` generic on `_with_retry`, `cast()` wrapping for SDK and `json.loads` Any leakage, typed lifespan/SSE generators in `main.py`.
- **Coverage**: CI runs `pytest --cov=app --cov-report=xml`, `scripts/coverage_badge.py` generates `coverage.json` for a self-hosted shields.io endpoint badge (no Codecov account required).
- **Docker**: multi-stage `Dockerfile` (deps → runtime), `docker-compose.yml` with healthcheck and persistent `./data` volume, `.dockerignore`, `.env.example` documenting every env var. `app/config.py` learned to read `.env` without adding a dependency.
- **Repo hygiene**: `.gitattributes` enforces LF line endings; extended `.gitignore` for `.env`, `.mypy_cache/`, `.ruff_cache/`, `coverage.xml`, `dist/`, `build/`.

### Bug fixes
- **CV upload preference key mismatch** (`/api/upload-cv`): the handler checked `summary.get("ruoli_preferiti")` but both the LLM prompt and the heuristic returned `preferred_roles`, so the per-user roles preference was never persisted on upload. Now reads and writes `preferred_roles`.
- **CV content validation**: `validate_cv_content()` rejects uploads under 200 chars or missing common CV keywords (HTTP 422), preventing junk PDFs from polluting the profile store.
- **CV upload deduplication**: migration `003_candidate_profile_hash.py` adds `content_hash` + index; re-uploading the same file now returns the existing `profile_id` instead of creating a duplicate row.
- **Bundle: missing `tls_client` DLL**: `JobFinder.spec` now `collect_data_files("tls_client")` so jobspy's TLS native lib (`tls-client-64.dll`) ships with the executable. Without this fix the EXE crashed at first scrape import with `FileNotFoundError`.
- **Bundle: migrations not discovered**: `app/migrations/*.py` added to spec `datas`; `pkgutil.iter_modules(__path__)` requires real files on disk (the PYZ-only inclusion via `collect_submodules` is not enough), so previous bundles raised `sqlite3.OperationalError: no such table: preferences` on first launch.
- **Bundle: `web/` static dir not found**: `create_app` now resolves `web_dir` from `sys._MEIPASS` when frozen (PyInstaller). Workspace dir holds only user-writable state (`data/`, `.env`, `cv.md`); read-only assets live inside the bundle.

### Refactor
- `web/app.js`: extracted i18n into `web/modules/i18n.js` with a `onLanguageChange` callback registry, dropping ~80 LOC from the main entry. Chat / scan / kanban / recommendations splits remain on the follow-up list.

### Docs
- README slimmed to 4 demo screenshots, accurate test count (122) and i18n key count (259), refreshed Project structure tree, new Rate limiting and Database migrations sections, expanded Mermaid architecture diagram (rate_limit, migrations, roles_shortlist, chat memory).
- `CONTRIBUTING.md`, `SECURITY.md`, `Makefile` added; `DOCS/schema.md` and `DOCS/security.md` tracked and linked from README.
- New `tests/e2e/readme-demo-gif.spec.js` records an animated hero GIF via Playwright + ffmpeg (run via `npm run record-demo`).
- Empty `tests/e2e/screenshots.spec.js` deleted; `readme-cv-showcase.spec.js` renamed to `manual-cv-flow.spec.js` and restricted to the Italian CV.

### Standalone Windows bundle
- `scripts/launch_exe.py`: PyInstaller entry point. Resolves a writable workspace next to the executable, sets `JOBFINDER_WORKSPACE` before importing `app.main`, opens the default browser when uvicorn is ready, runs without `reload`.
- `JobFinder.spec`: PyInstaller config with two analyses (`launch_exe` → `JobFinder.exe`, `updater` → `Updater.exe`) merged via `MERGE` so dependencies are stored once. Hidden imports cover `pkgutil`-discovered submodules (`app.migrations`, `app.providers`) and the LLM SDKs imported lazily inside try/except.
- `scripts/build_exe.py` + `make build-exe`: idempotent local build that wipes `build/`, runs PyInstaller, and zips `dist/JobFinder/` into `dist/JobFinder-windows.zip`.
- **Banner signup links**: the no-API-key sticky banner now renders three CTAs (Cerebras free key, Groq key, Open Settings) so a non-developer can register in 30 s without reading docs. 4 new i18n keys × 5 locales (`banner.signupHint`, `signupCerebras`, `signupGroq`, `openSettings`).
- **README "For non-developers (Windows)"**: 5-step download → extract → run → register → paste-key flow, plus SmartScreen workaround. New shields.io release badge linking to the latest GitHub release.
- **CI release workflow** (`.github/workflows/release.yml`): on tag `v*` push, runs `python scripts/build_exe.py` on `windows-latest` and uploads `JobFinder-windows.zip` as a release asset (auto-generated notes). `workflow_dispatch` trigger uploads it as an artifact instead, for dry runs.

### Self-update (standalone bundle)
- `app/update_sync.py`: `sync_install_dir(source, target)` copies a freshly-extracted bundle over the install dir, skipping any path whose first component is `data`, `.env`, or `.env.local`. User DB, secrets, settings, and logs are guaranteed to survive every update.
- `scripts/updater.py` (bundled as `Updater.exe`): waits for the parent JobFinder PID to exit (Windows `OpenProcess` / POSIX `os.kill(pid, 0)`), downloads the latest `*windows.zip` asset from GitHub Releases, extracts to a temp dir, runs the sync, restarts JobFinder.exe. Every step logged to `data/logs/updater.log`. Failures leave the install dir untouched.
- `POST /api/update/start` (`app/main.py`): refuses with 409 in dev mode or when already on latest, refuses with 500 if `Updater.exe` is missing. On success, spawns the updater detached and schedules `os._exit(0)` 0.8 s later so the response flushes and files unlock.
- `app/version.py:get_version_info` reports `frozen: bool` so the frontend picks the right update flow.
- Frontend update banner branches on `info.frozen`: bundle users see a progress modal that polls `/api/health` every 2 s, detects the file-replacement outage window, and auto-reloads the page when the new process answers. Dev users keep the existing `git pull && pip install` flow.
- 6 new unit tests (`tests/unit/test_update_sync.py`) cover: data dir survives, app/ files are replaced, brand new files land, `.env` stays put, source `data/` subtree is ignored, missing source raises.
