"""Informative del modulo partenariati (WP4, decisioni Q2 e Q13).

Testi SEGNAPOSTO versionati, da far rivedere al legale prima di accendere il
flag in produzione (per questo la prima riga di ciascuno lo dichiara). Chi
concede il consenso invia la versione che ha letto: se non è quella corrente
il servizio risponde 409 `informativa_superata` e il frontend ricarica il
testo. Una nuova versione non invalida i consensi già dati: compare solo un
banner di riconferma (`riconsenso_suggerito`, salvo diverso parere del legale).

Regole per chi modifica i testi: italiano semplice, del tu, mai «Famiglia»
(il gruppo di account è l'«azienda»); ogni cambio di sostanza fa salire la
versione, perché il registro dei consensi (`partner_consents`) conserva la
versione accettata come prova.

Versione 2 (WP9, decisione di Michele sulla verifica dell'identità): verifica
dell'identità da parte della piattaforma su richiesta, nome dell'azienda solo
se scelto e verificato, rivelazione dopo l'accettazione solo tra aziende
entrambe verificate, accesso dello staff di moderazione ai messaggi segnalati,
conservazione di segnalazioni e verifiche. Chi aveva acconsentito alla v1
vede il banner di riconferma.
"""

from app.schemas.partner_profile import InformativaPartnerOut

INFORMATIVA_PARTNER_VERSIONE = "2026-10-bozza-2"

INFORMATIVA_PARTNER_TESTO = """\
[BOZZA — DA RIVEDERE CON IL LEGALE]

# Visibilità come partner: informativa

Versione 2026-10-bozza-2.

## Chi tratta i dati
Il titolare del trattamento è il gestore della piattaforma BandoFit [ragione \
sociale, sede e contatto privacy da completare].

## A cosa serve
Se acconsenti, la tua azienda compare tra i partner suggeriti alle altre \
aziende della piattaforma che cercano con chi partecipare a un bando, e può \
ricevere inviti. Senza il tuo consenso non compare mai.

## Cosa vedono le altre aziende
- la regione della sede e le regioni o i paesi che ti interessano;
- il settore di attività (sezione ATECO) e la classe dimensionale, presi dal \
Registro Imprese;
- le fasce dei bilanci (per esempio «fatturato tra 500.000 € e 2 milioni»), \
mai gli importi esatti;
- le competenze, le esperienze, le certificazioni e le descrizioni che scrivi tu;
- il nome dell'azienda (la denominazione del Registro Imprese), solo se scegli di \
mostrarlo e solo se la piattaforma ha verificato l'identità dell'azienda. Vale \
anche per le call di partenariato che pubblichi con il nome. Se la verifica \
viene revocata, il nome non si mostra più e l'azienda torna anonima.

Se scegli che l'azienda resti anonima vedono meno: niente nome, solo la fascia di \
fatturato, esperienze senza anno né ruolo, certificazioni solo per categoria, \
niente infrastrutture.

Prima che tu accetti un contatto nessuno vede i tuoi recapiti: né email, né \
telefono, né sito. Per questo nei testi del profilo non puoi scriverli.

## Verifica dell'identità
La verifica la chiede il titolare dalla pagina Azienda, se vuole. Un \
amministratore della piattaforma controlla che chi la chiede rappresenti davvero \
l'azienda, usando i recapiti ufficiali del Registro Imprese (per esempio una \
telefonata alla sede o una PEC). Registriamo la richiesta, l'esito, il metodo \
usato e chi ha deciso. La verifica serve a mostrare il nome dell'azienda e a \
rivelare le identità dopo un'accettazione. Si revoca da sola se cambiano la \
partita IVA o la ragione sociale, o se l'azienda viene eliminata o archiviata; \
l'amministratore può revocarla indicando il motivo.

## Cosa si vede dopo che accetti un contatto
Quando accetti un invito o una candidatura potete scrivervi nella chat della \
piattaforma. Le identità si rivelano solo se entrambe le aziende hanno \
l'identità verificata dalla piattaforma: allora l'altra azienda vede la ragione \
sociale, il sito e la PEC presi dal Registro Imprese, il nome e il ruolo del \
referente. Se anche una sola delle due non è verificata, sito, PEC e referente \
non si rivelano e restate anonime l'una per l'altra, salvo il nome che \
un'azienda verificata ha scelto di mostrare nel profilo o in una call (quello \
lo vedono tutte le aziende); in chat potete presentarvi voi, se volete. Se in \
seguito la verifica di un'azienda viene revocata, la sua identità non si mostra \
più. L'email personale del referente non viene mai mostrata.

## Segnalazioni e moderazione
Chiunque può segnalare una call, un profilo o un messaggio. Lo staff di \
moderazione della piattaforma esamina le segnalazioni. Per un messaggio \
segnalato vede quel messaggio e una finestra di messaggi vicini della stessa \
conversazione; la conversazione intera solo se serve a decidere, indicando una \
motivazione che resta registrata. Gli accessi ai messaggi vicini e alla \
conversazione intera, e ogni decisione, restano registrati. Se una decisione \
riguarda un tuo contenuto ricevi la motivazione e puoi presentare ricorso.

## Il referente
Il referente è il titolare dell'azienda sulla piattaforma. Puoi proporre \
un'altra persona che ha accesso all'azienda: diventa referente solo se accetta \
lei stessa, dopo aver letto la sua informativa, e può rinunciare quando vuole.

## Consulenti incaricati
Se l'azienda è gestita da un consulente sulla piattaforma, o se chiedi una \
consulenza a un progettista, il consulente vede i dati del profilo che servono \
a seguirti. Anche loro non vedono contatti che tu non abbia già rivelato.

## Collegamenti tra aziende
Per non suggerirti aziende collegate alla tua (stesso gruppo, soci in comune) \
confrontiamo i dati di soci e partecipazioni del Registro Imprese attraverso \
codici cifrati non reversibili: da quei codici nessuno può risalire ai nomi.

## Base giuridica
Il tuo consenso, per la visibilità come partner. Puoi non darlo: la piattaforma \
resta utilizzabile, ma l'azienda non compare tra i partner suggeriti. Per la \
verifica dell'identità e per la moderazione dei contenuti [base giuridica da \
definire con il legale].

## Revoca
Puoi revocare il consenso in qualunque momento dalla pagina Azienda. La revoca \
è immediata: da quel momento non compari più nei suggerimenti. Le conversazioni \
già avviate restano.

## Per quanto tempo
Il profilo partner resta finché non lo modifichi o elimini l'azienda. Il \
registro dei consensi (chi ha acconsentito o revocato, quando e con quale \
versione di questa informativa) si conserva per 5 anni dopo la revoca, come \
prova. Le segnalazioni, le decisioni di moderazione e i ricorsi si conservano \
per 24 mesi. Il registro delle verifiche dell'identità (richieste, esiti e \
revoche) si conserva come prova anche dopo l'eliminazione dell'azienda [durata \
da definire con il legale].

## I tuoi diritti
Puoi chiedere di accedere ai dati, correggerli, cancellarli, limitarne l'uso, \
opporti al trattamento e riceverne una copia, scrivendo al contatto privacy \
indicato sopra. Puoi anche presentare reclamo al Garante per la protezione dei \
dati personali.
"""

INFORMATIVA_REFERENTE_VERSIONE = "2026-10-bozza-2"

INFORMATIVA_REFERENTE_TESTO = """\
[BOZZA — DA RIVEDERE CON IL LEGALE]

# Referente per i partenariati: informativa

Versione 2026-10-bozza-2.

Il titolare dell'azienda ti ha proposto come referente per i partenariati. \
Se accetti:
- quando l'azienda accetta un invito o una candidatura, l'altra azienda può \
scriverti nella chat della piattaforma; vede il tuo nome e il tuo ruolo solo se \
entrambe le aziende hanno l'identità verificata dalla piattaforma;
- la tua email personale non viene mai mostrata;
- prima dell'accettazione di un contatto nessuno vede i tuoi dati;
- se un messaggio viene segnalato, lo staff di moderazione della piattaforma \
vede quel messaggio e quelli vicini; la conversazione intera solo se serve a \
decidere, con una motivazione registrata.

Titolare del trattamento è il gestore della piattaforma BandoFit [ragione \
sociale, sede e contatto privacy da completare]. La base giuridica è il tuo \
consenso: puoi rifiutare, e se accetti puoi rinunciare in qualunque momento; da \
quel momento il referente torna il titolare dell'azienda.

Conserviamo la tua accettazione e la rinuncia (quando e con quale versione di \
questa informativa) per 5 anni dopo la rinuncia, come prova. Puoi chiedere di \
accedere ai tuoi dati, correggerli o cancellarli scrivendo al contatto privacy, \
e presentare reclamo al Garante per la protezione dei dati personali.
"""


def informativa_out() -> InformativaPartnerOut:
    """Le due informative nella forma di `GET /partenariati/informativa`."""
    return InformativaPartnerOut(
        versione=INFORMATIVA_PARTNER_VERSIONE,
        testo=INFORMATIVA_PARTNER_TESTO,
        referente_versione=INFORMATIVA_REFERENTE_VERSIONE,
        referente_testo=INFORMATIVA_REFERENTE_TESTO,
    )
