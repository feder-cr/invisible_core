"""Cosa sa fare DAVVERO l'uscita di questa sessione, misurato prima del lancio.

⛔ QUESTO E' PRODOTTO, NON APPARATO DI MISURA, e la differenza si vede in una
riga: cio' che questo modulo risponde DECIDE come il browser viene configurato.
Nasce nel banco (`tests/lib/capacita_uscita.py`, 2026-08-25) e ci e' rimasto un
giorno; adesso quel file e' un rimando a questo, perche' un fatto calcolato in
due posti diverge.

**La regola che il proprietario ha dettato il 2026-08-25, e che semplifica
tutto:** cio' che si rileva PRIMA di avviare il browser e' la verita' della
sessione, ed e' assoluta per la sua durata. Non si ricontrolla, non si insegue
la deriva, non interessa se il proxy resti appiccicoso. Si misura una volta e si
configura di conseguenza.

⛔ LE CAPACITA' SONO UNA PROPRIETA' DEL GATEWAY, NON DELLA SESSIONE, ed e' cio'
che rende praticabile sondarle. Misurato il 2026-08-25 su 500 sessioni: 95 peer
vivi su un provider rispondono tutti `rep=7`, 100 su 100 su un altro, e i due
gateway dello stesso account condividono 71 indirizzi su 71 dando risposte
DIVERSE. Cambiare peer non cambia la risposta; cambiare gateway si'. Quindi si
sonda una volta per endpoint e si riusa, invece di pagare secondi a ogni lancio.
Costo misurato: 4-19 secondi, una volta ogni 24 ore per gateway.

⛔ E L'ASSENZA DI PROVA CADE SEMPRE DALLA PARTE PRUDENTE. Se la sonda non
risponde, o risponde in modo ambiguo, la configurazione e' quella di sempre.
Una capacita' si sfrutta solo quando e' DIMOSTRATA, perche' sbagliare in
quella direzione costa il messaggio peggiore che un rilevatore possa scrivere:
misurato leggendo il codice di un rilevatore vero, un browser che non emette
candidati si prende "Javascript is manipulated" invece di "VPN detected"
(`docs_research/scrapfly-re/00-WEBRTC-LEAK.md`).

Nessuna funzione qui stampa una credenziale.
"""
from __future__ import annotations

import json
import os
import re
import socket
import struct
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

#: Dove si ricorda cio' che si e' misurato. Fuori dall'albero git, come gli
#: altri segreti: la chiave contiene l'hostname del provider.
CACHE = os.path.join(os.environ.get("TEMP", "/tmp"), "capacita_uscita.json")

#: Quanto vale una misura prima di rifarla. Le capacita' di un gateway cambiano
#: quando il fornitore cambia infrastruttura, non di ora in ora.
VALIDITA_S = 24 * 3600

_REP = {0: "ok", 1: "guasto generale", 2: "non consentito", 3: "rete irraggiungibile",
        4: "host irraggiungibile", 5: "conn rifiutata", 6: "ttl scaduto",
        7: "comando non supportato", 8: "tipo indirizzo non supportato"}


# --------------------------------------------------------------------------
# I mattoni: una domanda ciascuno, nessuno interpreta.
# --------------------------------------------------------------------------

def _saluta_socks5(s, utente: str, segreto: str) -> Optional[str]:
    s.sendall(b"\x05\x01\x02")
    r = s.recv(2)
    if len(r) != 2 or r[0] != 5:
        return "saluto non SOCKS5"
    if r[1] == 2:
        u, p = utente.encode(), segreto.encode()
        s.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
        a = s.recv(2)
        if len(a) != 2 or a[1] != 0:
            return "autenticazione rifiutata"
    elif r[1] == 0xFF:
        return "nessun metodo di autenticazione accettabile"
    return None


def _connect_socks5(s, host: str, porta: int) -> Optional[int]:
    h = host.encode()
    s.sendall(b"\x05\x01\x00\x03" + bytes([len(h)]) + h + struct.pack("!H", porta))
    r = s.recv(10)
    return r[1] if len(r) >= 2 and r[0] == 5 else None


def uscita_tcp(proxy: Dict[str, str], timeout: float = 25) -> Optional[str]:
    """L'indirizzo da cui esce il TCP, letto in chiaro su una connessione sola."""
    u = urlparse(proxy["server"])
    try:
        s = socket.create_connection((u.hostname, u.port), timeout=timeout)
        s.settimeout(timeout)
        # ⛔ Uno schema `https` significa TLS verso il PROXY, prima di
        # qualunque richiesta. La prima stesura parlava HTTP in chiaro a una
        # porta TLS e tornava `None`, che il riassunto mostrava come "sticky
        # non determinato": un difetto dello strumento travestito da proprieta'
        # dell'endpoint. Il certificato si verifica sul serio, perche' e' quello
        # che distingue l'host giusto da uno che gli somiglia.
        if u.scheme == "https":
            import ssl
            s = ssl.create_default_context().wrap_socket(s, server_hostname=u.hostname)
        try:
            if u.scheme.startswith("socks"):
                if _saluta_socks5(s, proxy.get("username", ""), proxy.get("password", "")):
                    return None
                if _connect_socks5(s, "api.ipify.org", 80) != 0:
                    return None
                s.sendall(b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\n"
                          b"Connection: close\r\nUser-Agent: curl/8\r\n\r\n")
            else:
                import base64
                cred = base64.b64encode(
                    ("%s:%s" % (proxy.get("username", ""),
                                proxy.get("password", ""))).encode()).decode()
                s.sendall(("GET http://api.ipify.org/ HTTP/1.1\r\n"
                           "Host: api.ipify.org\r\nProxy-Authorization: Basic %s\r\n"
                           "Connection: close\r\nUser-Agent: curl/8\r\n\r\n" % cred).encode())
            dati = b""
            while len(dati) < 4096:
                p = s.recv(4096)
                if not p:
                    break
                dati += p
            m = re.search(rb"\b(?:\d{1,3}\.){3}\d{1,3}\b", dati.split(b"\r\n\r\n")[-1])
            return m.group(0).decode() if m else None
        finally:
            s.close()
    except OSError:
        return None


def udp_associate(proxy: Dict[str, str], timeout: float = 25):
    """`(supportato, spiegazione)`. Solo SOCKS5: in HTTP il comando non esiste."""
    u = urlparse(proxy["server"])
    if not u.scheme.startswith("socks"):
        return False, "schema %s: SOCKS UDP ASSOCIATE non esiste nel protocollo" % u.scheme
    try:
        s = socket.create_connection((u.hostname, u.port), timeout=timeout)
        s.settimeout(timeout)
        try:
            e = _saluta_socks5(s, proxy.get("username", ""), proxy.get("password", ""))
            if e:
                return False, e
            s.sendall(b"\x05\x03\x00\x01" + b"\x00" * 4 + b"\x00\x00")
            r = s.recv(4)
            if not r:
                return False, "il gateway ha chiuso senza rispondere"
            if len(r) < 4 or r[0] != 5:
                return False, "risposta non interpretabile"
            if r[1] != 0:
                return False, "rep=%d %s" % (r[1], _REP.get(r[1], "?"))
            atyp = r[3]
            ind = (socket.inet_ntoa(s.recv(4)) if atyp == 1 else
                   s.recv(s.recv(1)[0]).decode() if atyp == 3 else
                   socket.inet_ntop(socket.AF_INET6, s.recv(16)))
            porta = struct.unpack("!H", s.recv(2))[0]
            if ind in ("0.0.0.0", "::"):
                ind = u.hostname
            return True, "%s:%d" % (ind, porta)
        finally:
            s.close()
    except OSError as ex:
        return False, "errore %s" % type(ex).__name__


def uscita_udp(proxy: Dict[str, str], timeout: float = 12) -> Optional[str]:
    """L'indirizzo da cui esce l'UDP, chiesto a STUN. `None` se UDP non passa.

    E' la meta' che quasi nessuno misura, e senza la quale "il proxy porta UDP"
    non basta: se questo indirizzo non e' lo stesso del TCP, usarlo per WebRTC
    produce due indirizzi in una sessione, cioe' il disaccordo che i rilevatori
    cercano.
    """
    ok, dove = udp_associate(proxy)
    if not ok:
        return None
    host, _, porta = dove.rpartition(":")
    u = urlparse(proxy["server"])
    try:
        ctrl = socket.create_connection((u.hostname, u.port), timeout=timeout)
        ctrl.settimeout(timeout)
        if _saluta_socks5(ctrl, proxy.get("username", ""), proxy.get("password", "")):
            ctrl.close()
            return None
        ctrl.sendall(b"\x05\x03\x00\x01" + b"\x00" * 4 + b"\x00\x00")
        ctrl.recv(10)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        try:
            h = b"stun.l.google.com"
            testa = b"\x00\x00\x00\x03" + bytes([len(h)]) + h + struct.pack("!H", 19302)
            s.sendto(testa + b"\x00\x01\x00\x00\x21\x12\xa4\x42" + os.urandom(12),
                     (host, int(porta)))
            d, _ = s.recvfrom(2048)
            if len(d) < 10 or d[2] != 0:
                return None
            atyp, i = d[3], 4
            i += 4 if atyp == 1 else (1 + d[4] if atyp == 3 else 16)
            c = d[i + 2:]
            if len(c) < 20 or c[0:2] != b"\x01\x01":
                return None
            j, fine = 20, 20 + struct.unpack("!H", c[2:4])[0]
            while j + 4 <= min(fine, len(c)):
                t, ln = struct.unpack("!HH", c[j:j + 4])
                v = c[j + 4:j + 4 + ln]
                if t == 0x0020 and len(v) >= 8:
                    ip = bytes(a ^ b for a, b in zip(v[4:8], b"\x21\x12\xa4\x42"))
                    return socket.inet_ntoa(ip)
                j += 4 + ln + ((4 - ln % 4) % 4)
            return None
        finally:
            s.close()
            ctrl.close()
    except OSError:
        return None


def ha_ipv6(proxy: Dict[str, str], timeout: float = 20) -> Optional[bool]:
    """`True`/`False`, oppure `None` se la domanda non e' stata risolta.

    ⛔ Il `None` NON e' un `False`: un fallimento di rete e una uscita senza
    IPv6 sono cose diverse, e appiattirle farebbe spegnere IPv6 per un timeout.
    """
    u = urlparse(proxy["server"])
    if not u.scheme.startswith("socks"):
        return None
    try:
        s = socket.create_connection((u.hostname, u.port), timeout=timeout)
        s.settimeout(timeout)
        try:
            if _saluta_socks5(s, proxy.get("username", ""), proxy.get("password", "")):
                return None
            if _connect_socks5(s, "api6.ipify.org", 80) != 0:
                return False
            s.sendall(b"GET / HTTP/1.1\r\nHost: api6.ipify.org\r\n"
                      b"Connection: close\r\nUser-Agent: curl/8\r\n\r\n")
            dati = b""
            while len(dati) < 4096:
                p = s.recv(4096)
                if not p:
                    break
                dati += p
            corpo = dati.split(b"\r\n\r\n")[-1]
            return b":" in corpo and len(corpo.strip()) > 2
        finally:
            s.close()
    except OSError:
        return None


def e_sticky(proxy: Dict[str, str], giri: int = 6):
    """`(sticky, indirizzi)`. Sei letture con la STESSA credenziale.

    Sei e non due: due estrazioni uguali di fila da un'urna non dimostrano che
    l'urna abbia una pallina sola, ed e' un errore che questo progetto ha gia'
    fatto e registrato.
    """
    visti = [uscita_tcp(proxy) for _ in range(giri)]
    buoni = [x for x in visti if x]
    distinti = sorted(set(buoni))
    if not buoni:
        return None, []
    return len(distinti) == 1, distinti


# --------------------------------------------------------------------------
# Il verdetto, e la memoria
# --------------------------------------------------------------------------

def _chiave(proxy: Dict[str, str]) -> str:
    u = urlparse(proxy["server"])
    return "%s://%s:%s" % (u.scheme, u.hostname, u.port)


def misura(proxy: Dict[str, str],
           *, uscita_tcp_nota: Optional[str] = None) -> Dict[str, Any]:
    """Tutto quello che si puo' sapere di un'uscita senza lanciare un browser.

    ``uscita_tcp_nota`` evita di rimisurare cio' che il chiamante ha gia'.
    ``prepare_session_geo`` scopre l'IP di uscita per il fuso e la lingua, con
    un giro attraverso il proxy: rifarlo qui sarebbe lo stesso fatto calcolato
    in due punti, e un giro in piu' verso un endpoint di echo, che e' traffico
    non-di-browser sulla stessa uscita.

    ⛔ LA STICKINESS NON E' PIU' QUI, ed e' una decisione del proprietario del
    2026-08-25: *"cosa succede se i proxy sono sticky o no non ti interessa, per
    te la verita' e' quella che rilevi prima di avviare ed e' assoluta per la
    sessione"*. Toglierla costa zero e vale due volte:

    * era **sei giri di rete su otto** del costo di questa sonda;
    * e **mentiva**. Diceva `sticky = si` per tutti e quattro i fornitori, mentre
      lo stesso endpoint era stato misurato ruotare **8 volte in 25 minuti**
      (`27-retail-network-parity.md` sezione 17). Sei letture consecutive durano
      pochi secondi, la rotazione avviene su una scala di minuti: quel campo non
      poteva vedere il fenomeno da cui prendeva il nome.

    ``e_sticky`` resta disponibile per chi voglia MISURARE la rotazione con una
    finestra adeguata, ma non entra piu' in nessuna decisione del prodotto.
    """
    ip_tcp = uscita_tcp_nota or uscita_tcp(proxy)
    ok_udp, perche = udp_associate(proxy)
    ip_udp = uscita_udp(proxy) if ok_udp else None
    return {
        "endpoint": _chiave(proxy),
        "schema": urlparse(proxy["server"]).scheme,
        "uscita_tcp": ip_tcp,
        "udp": ok_udp,
        "udp_perche": perche,
        "uscita_udp": ip_udp,
        # ⛔ la riga che decide se UDP sia USABILE, non solo CONCESSO.
        # Misurato: un gateway concede l'UDP ASSOCIATE e restituisce un relay,
        # e attraverso quel relay lo STUN non risponde. Concesso non e' usabile.
        "udp_coerente": (bool(ip_udp) and ip_udp == ip_tcp) if ok_udp else None,
        "ipv6": ha_ipv6(proxy),
        "quando": int(time.time()),
    }


def capacita(proxy: Dict[str, str], *, rimisura: bool = False,
             uscita_tcp_nota: Optional[str] = None) -> Dict[str, Any]:
    """La misura, dalla memoria se e' recente. Le capacita' sono del gateway."""
    chiave = _chiave(proxy)
    memoria = {}
    try:
        with open(CACHE, encoding="utf-8") as f:
            memoria = json.load(f)
    except (OSError, ValueError):
        pass
    voce = memoria.get(chiave)
    if voce and not rimisura and (time.time() - voce.get("quando", 0)) < VALIDITA_S:
        voce["da_memoria"] = True
        return voce
    voce = misura(proxy, uscita_tcp_nota=uscita_tcp_nota)
    memoria[chiave] = voce
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(memoria, f, indent=1)
    except OSError:
        pass
    voce["da_memoria"] = False
    return voce


def riassumi(c: Dict[str, Any]) -> str:
    def _s(v):
        return "si" if v is True else ("no" if v is False else "non determinato")
    return ("%-46s udp=%-16s coerente=%-16s ipv6=%s"
            % (c["endpoint"], _s(c["udp"]), _s(c["udp_coerente"]), _s(c["ipv6"])))
