"""Proxy translation: ONE reading of the endpoint, ONE road to the engine.

⛔ THERE USED TO BE THREE ROADS FOR ONE FACT - where does this session's
traffic go - and the scheme picked between them. SOCKS was routed by writing
``network.proxy.*``; HTTP/HTTPS was handed back for the Playwright driver to
route per channel; HTTP/HTTPS without a driver got different prefs and a
refusal whenever credentials were present. When the Node driver was removed the
middle road stopped existing, and ``proxy=`` with an ``http`` scheme was
accepted and dropped: the page resolved its own DNS and went out on the host
address while the timezone, the locale and the WebRTC candidate had all been
resolved THROUGH the proxy. Announcing one country and connecting from another
is the exact signal this package exists to avoid, manufactured by the package.

Reported from outside on 2026-08-30, and the report is what settled the design:
adding the missing call would have left two roads, which is the shape that
produced the defect. So the other roads are DELETED, not fixed.

**The one road is the engine's ``Browser.setBrowserProxy``.** It has always been
there, it carries credentials, and it covers ``http``, ``https``, ``socks`` and
``socks4`` alike - so the scheme decides nothing any more. ``parse_proxy`` reads
the endpoint once; whoever holds the protocol connection sends the command. A
caller with no connection is not given a second road: ``build_launch_plan``
refuses a proxy and names the wrapper.

What stays here is what was never routing: the prefs that close the channels a
proxied session can still leak through.
"""
from __future__ import annotations

from typing import Any, Dict, NamedTuple, Optional


#: What a caller may write on the left, the four names the engine's
#: ``setBrowserProxy`` declares on the right. ``socks5`` is what everybody
#: writes and is NOT one of them.
_PROXY_TYPES = {"http": "http", "https": "https",
                "socks": "socks", "socks5": "socks", "socks4": "socks4"}

#: The port Playwright documents as the default for each scheme.
_DEFAULT_PORT = {"http": 80, "https": 443, "socks": 1080, "socks4": 1080}


class ProxyEndpoint(NamedTuple):
    """One proxy, in the terms the engine uses. ``type`` is one of the four."""

    type: str
    host: str
    port: int
    username: str
    password: str
    bypass: tuple

    @property
    def is_socks(self) -> bool:
        return self.type in ("socks", "socks4")

    @property
    def has_credentials(self) -> bool:
        return bool(self.username or self.password)

    def as_engine_command(self) -> Dict[str, Any]:
        """The params of ``Browser.setBrowserProxy`` / ``setContextProxy``.

        Credentials are OMITTED rather than sent empty: the engine declares
        them Optional, and an empty string is a value, not an absence.
        """
        params: Dict[str, Any] = {"type": self.type, "host": self.host,
                                  "port": self.port, "bypass": list(self.bypass)}
        if self.username:
            params["username"] = self.username
        if self.password:
            params["password"] = self.password
        return params


def parse_proxy(proxy: Optional[Dict[str, Any]]) -> Optional[ProxyEndpoint]:
    """The ONE place that reads what the caller wrote. Raises, never guesses.

    ``None`` means "no proxy was asked for", which is the only case a caller may
    treat as "carry on". Anything present and unreadable RAISES, because the
    caller cannot otherwise tell that apart from a proxy that was silently
    dropped - and a dropped proxy has to stop the launch.
    """
    if not proxy:
        return None
    server = str(proxy.get("server") or "").strip()
    if not server or server.lower() == "direct://":
        return None

    rest = server
    scheme = "http"                     # Playwright's default for a bare host
    if "://" in rest:
        scheme, rest = rest.split("://", 1)
        scheme = scheme.lower()
    if scheme not in _PROXY_TYPES:
        raise ValueError(
            "proxy server %r uses scheme %r, which the engine cannot express. "
            "It takes one of: %s"
            % (server, scheme, ", ".join(sorted(_PROXY_TYPES))))
    kind = _PROXY_TYPES[scheme]

    rest = rest.split("/", 1)[0]
    if rest.startswith("["):            # an IPv6 literal keeps its brackets
        host, _, tail = rest.partition("]")
        host, port_text = host[1:], (tail[1:] if tail.startswith(":") else "")
    elif ":" in rest:
        # ⛔ `rpartition` ON THE COLON, and the branch has to be on whether a
        # colon EXISTS rather than on whether the head came back empty: with
        # the second test `http://:80` read as the host "80" on the default
        # port, which is a proxy nobody wrote. Caught by the known-bad case,
        # not by review.
        host, _, port_text = rest.rpartition(":")
    else:
        host, port_text = rest, ""
    if not host:
        raise ValueError("proxy server %r names no host" % server)

    if port_text:
        try:
            port = int(port_text)
        except ValueError:
            raise ValueError(
                "proxy server %r has a port that is not a number" % server)
        if not 1 <= port <= 65535:
            raise ValueError(
                "proxy server %r has a port outside 1-65535" % server)
    else:
        port = _DEFAULT_PORT[kind]

    bypass = proxy.get("bypass")
    if isinstance(bypass, str):
        bypass = [b.strip() for b in bypass.split(",") if b.strip()]
    return ProxyEndpoint(type=kind, host=host, port=port,
                         username=str(proxy.get("username") or ""),
                         password=str(proxy.get("password") or ""),
                         bypass=tuple(bypass or ()))


def configure_proxy(
    proxy: Optional[Dict[str, str]],
    prefs: Dict[str, Any],
) -> Optional[Dict[str, str]]:
    """Validate the endpoint, close the leak channels, hand it on. NO routing.

    ⛔ THIS WRITES NO ``network.proxy.*``, AND THAT IS THE POINT. Routing is the
    engine command, sent by whoever holds the connection, for every scheme
    alike - see the module docstring for the three roads this replaced and the
    defect they produced.

    What remains is what was never routing: the prefs below close the channels
    a proxied session can still leak through. They were already applied to both
    schemes and they still are, unchanged.

    Returns the endpoint dict so the caller can hand it to the client that
    sends the command, ``None`` when no proxy was asked for. An unreadable
    endpoint RAISES: a caller cannot tell "none" from "dropped", and dropped
    has to stop the launch.
    """
    if parse_proxy(proxy) is None:
        return None

    # ⛔ QUI E' STATA PROVATA `network.dns.disableIPv6 = True` E TOLTA: E' INERTE
    # DIETRO UN PROXY, e una patch che non sposta nessuna misura non e' una
    # patch.
    #
    # L'idea era rendere il profilo IPv4-only per intero: i candidati WebRTC
    # IPv6 li scartiamo gia' dietro proxy (l'srflx IPv6 non e' offuscato
    # dall'mDNS e porterebbe l'indirizzo globale VERO), ma l'HTTP poteva ancora
    # uscire in IPv6 - misurato il 2026-08-25 su un peer residenziale
    # dual-stack: dichiaravamo `73.209.132.45` mentre la stessa pagina
    # raggiungeva un servizio di echo su `2603:300a:92e:8600:...`.
    #
    # La pref pero' non cambia niente, e il controfattuale lo dimostra: applicata
    # e RILETTA dal profilo del browser (`network.dns.disableIPv6 true`), l'echo
    # continuava a uscire sullo stesso IPv6. La ragione e' `socks_remote_dns`:
    # Firefox consegna al proxy il NOME, non un indirizzo, quindi e' il proxy a
    # risolvere e a scegliere la famiglia. Il resolver di Firefox non viene
    # nemmeno interpellato, e lo stesso vale per un proxy HTTP, dove il nome
    # viaggia dentro la CONNECT.
    #
    # Conseguenza da sapere: **la famiglia di indirizzi dietro un proxy non la
    # decidiamo noi.** Se il peer ha IPv6 e il sito e' dual-stack, usciamo in
    # IPv6 mentre WebRTC annuncia un IPv4. Chiuderlo davvero vuol dire
    # dichiarare ANCHE un srflx IPv6 con l'uscita IPv6 del proxy (scopribile:
    # `api6.ipify.org` attraverso il proxy risponde), non spegnere IPv6 da
    # questo lato.

    risultato = dict(proxy)

    # ⛔ QUANDO IL PROXY INCIAMPA, IL RIPIEGO DI FIREFOX E' USCIRE IN CHIARO.
    #
    # `network.proxy.allow_bypass` vale `true` di default - letto dall'header
    # GENERATO della nostra build, `dist/include/mozilla/StaticPrefList_network.h`,
    # non dallo yaml - e un canale che chiede `bypassProxy` salta
    # `ResolveProxy()` del tutto (`netwerk/protocol/http/nsHttpChannel.cpp`, la
    # guardia `!BypassProxy()`). Quel canale poi risolve il nome col resolver
    # dell'utente, perche' senza `mProxyInfo` il DNS viene FORZATO con
    # `RESOLVE_IGNORE_SOCKS_DNS` (`DnsAndConnectSocket.cpp`; il commento
    # upstream lo dice: "force resolution despite global proxy-DNS
    # configuration"). Non e' solo DNS: e' una connessione DIRETTA, con l'IP
    # vero, e parte proprio nell'istante in cui il proxy sta gia' fallendo.
    #
    # Chi la usa: `services/settings/Utils.sys.mjs` (`fallbackOrReject`, su
    # onerror/ontimeout/onabort) e `TelemetrySend.sys.mjs` (`retryRequest`).
    # Remote Settings gira a ogni sessione, quindi l'occasione non e' rara.
    #
    # MISURATO il 2026-08-25 con un SOCKS5 locale che rifiuta apposta i due
    # host e registra ogni CONNECT - e il registro E' il controllo, perche' 98
    # CONNECT tutti per NOME dimostrano che la risoluzione remota funzionava:
    #
    #   senza questa pref  ->  43 rifiuti, e `firefox.settings.services.mozilla.com`
    #                          RISOLTO 13 volte sul resolver di casa
    #   con questa pref    ->  45 rifiuti, e ZERO risoluzioni locali; restano
    #                          i soli `127.0.0.1`, `local`, `localhost`, cioe'
    #                          lo stesso insieme di un SOCKS5 che non fallisce
    #
    # In entrambi i bracci la navigazione resta ok e il relay rilancia
    # normalmente detectportal, push.services, normandy e il resto: la pref
    # toglie il RIPIEGO, non il traffico.
    #
    # ⛔ VALE PER OGNI SCHEMA, HTTP COMPRESO, e sta DOPO il bivio apposta: le
    # due validazioni (la porta, le credenziali che non si possono consegnare)
    # devono poter sollevare PRIMA, lasciando il dict come l'hanno trovato. La
    # prima stesura la metteva prima e sporcava le prefs su un endpoint
    # malformato: due test esistenti sono diventati rossi e avevano ragione
    # loro.
    #
    # Sul ramo HTTP questo NON e' una pref di instradamento e non tocca
    # l'autenticazione: il contratto pinnato da
    # `test_il_percorso_che_delega_NON_cambia_comportamento` diceva "chi delega
    # non scrive prefs" per proteggere ROUTING e 407, e adesso lo dice con
    # quelle parole invece che con `prefs == {}`.
    prefs["network.proxy.allow_bypass"] = False

    # ⛔ E LA SECONDA META', PERCHE' LA PRIMA NON BASTA SU HTTP.
    #
    # `allow_bypass` chiude i CANALI che ripiegano in diretta. Ma tre superfici
    # non sono canali e chiamano `AsyncResolveNative` senza passare da nessun
    # filtro: `NetworkConnectivityService`, le sonde dell'euristica DoH, e il
    # resolver ICE. Il cancello del motore
    # (`netwerk/dns/DNSServiceBase.cpp`, `DNSForbiddenByActiveProxy`) le
    # fermerebbe, ma sa riconoscere solo un proxy scritto nelle
    # `network.proxy.*` - e sul ramo HTTP non ne scriviamo nessuna, perche'
    # instrada Playwright per canale. Quindi `network.proxy.type` resta 5 e il
    # cancello non si arma.
    #
    # Il motore non puo' dedurlo: glielo diciamo noi. E' la regola 1 - il core
    # dichiara, il motore obbedisce - applicata al DNS.
    #
    # MISURATO il 2026-08-25 dietro un proxy HTTP, due giri identici, con la
    # sola `allow_bypass` gia' attiva: restavano `example.org` 28,
    # `ipv4only.arpa` 12, `cloudflare-dns.com` 6 e - la peggiore -
    # `stunprobe.invalid` 6, che e' un nome scelto DALLA PAGINA via
    # `iceServers`. Dietro SOCKS gli stessi nomi facevano gia' 0.
    #
    # L'endpoint del proxy continua a risolversi: sia il livello SOCKS
    # (`nsSOCKSIOLayer`) sia `DnsAndConnectSocket` chiedono la loro risoluzione
    # con `RESOLVE_IGNORE_SOCKS_DNS`, che il cancello esenta per primo. Non e'
    # una deroga che aggiungiamo noi: e' quella che gia' regge il ramo SOCKS.
    prefs["zoom.stealth.dns.no_local_resolution"] = True

    # ⛔ E L'UDP DI ICE ESCE LO STESSO, se il server e' scritto come
    # INDIRIZZO NUMERICO invece che come nome. Il cancello DNS qui sopra non
    # lo puo' fermare, perche' un letterale non passa dal resolver.
    #
    # Misurato il 2026-08-26 su tre fornitori e due schemi: con uno STUN
    # numerico il motore riceve il MAPPED-ADDRESS con l'indirizzo VERO della
    # macchina, mentre la pagina vede l'uscita del proxy perche' il candidato
    # reale viene riscritto. Una fuga che nessun controllo lato pagina puo'
    # vedere, e che vede solo chi gestisce lo STUN.
    #
    # Le due prefs stanno qui insieme apposta: dicono al motore la stessa
    # cosa da due lati - dietro un proxy nulla esce per una strada che il
    # proxy non copre - e una decisione sola le emette entrambe.
    prefs["zoom.stealth.webrtc.no_direct_udp"] = True
    return risultato


#: ⛔ NO, e questa costante esiste perche' la risposta non e' ovvia e una
#: decisione ne dipende. Il motore ha il codice per farlo
#: (`netwerk/socket/nsSOCKSUDPIOLayer.{h,cpp}`, agganciato in `nsUDPSocket.cpp`)
#: ma e' dietro `network.proxy.socks_remote_udp`, che non impostiamo. Senza,
#: **l'UDP scavalca il proxy** e uno STUN raggiunto per quella via risponde con
#: l'indirizzo VERO della macchina.
#:
#: Chi la legge: `_geo._srflx_soppresso`. Smettere di dichiarare un srflx ha
#: senso solo se quello VERO nascera' con l'indirizzo giusto, e con l'UDP che
#: scavalca il proxy nascerebbe con l'indirizzo di casa. Cioe' la condizione
#: "l'uscita porta UDP coerente" NON basta: serve anche che il browser quell'UDP
#: lo mandi di la'.
#:
#: Il giorno in cui si accende la pref, questa costante si sposta con lei - e
#: sono lo stesso fatto scritto in un posto solo.
INSTRADIAMO_UDP_NEL_SOCKS = False

