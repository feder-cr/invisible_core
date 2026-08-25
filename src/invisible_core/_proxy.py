"""Proxy translation shared by sync and async launchers.

SOCKS proxies are driven entirely by the patched Firefox prefs (the
``nsProtocolProxyService`` patch reads ``network.proxy.socks_username``
and ``socks_password``). HTTP/HTTPS proxies go through Playwright's own
``proxy=`` kwarg so it can negotiate Basic auth.
"""
from __future__ import annotations

from typing import Any, Dict, Optional


_SOCKS_SCHEMES = ("socks5://", "socks4://", "socks://")


def configure_proxy(
    proxy: Optional[Dict[str, str]],
    prefs: Dict[str, Any],
    *,
    delegates_auth: bool = True,
) -> Optional[Dict[str, str]]:
    """Mutate ``prefs`` for SOCKS auth; return what to pass to Playwright.

    * ``None`` proxy → returns ``None``.
    * SOCKS proxy → writes the auth prefs and returns ``None`` (Playwright
      gets nothing; Firefox does the rest).
    * HTTP / HTTPS proxy → returns the dict unchanged for Playwright.

    ``delegates_auth`` is the caller stating a fact about ITSELF: whether it has
    a Playwright to hand an HTTP/HTTPS endpoint to. The wrapper does, so it
    leaves the default. ``build_launch_plan`` does not - it spawns the binary
    with ``subprocess`` - and passes ``False``.

    Why the caller declares it instead of this function guessing: for six weeks
    the direct-launch path called this function, discarded the returned dict
    because it had nowhere to put it, and launched a browser with NO proxy
    configuration at all. The session then went out on the host's own address
    while ``_geo`` had already resolved timezone and locale THROUGH the proxy,
    so the page announced one country and connected from another. Nothing
    raised, nothing logged. Same failure shape the no-port branch below was
    fixed for on 2026-08-01, on the other scheme.
    """
    if not proxy:
        return None

    server = (proxy.get("server") or "").strip()
    if not server or server.lower() == "direct://":
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

    if not _is_socks_scheme(server):
        risultato = _configure_http_like(proxy, prefs, server, delegates_auth)
    else:
        risultato = _configure_socks(proxy, prefs, server)

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


def _configure_socks(
    proxy: Dict[str, str],
    prefs: Dict[str, Any],
    server: str,
) -> None:
    """Le prefs di instradamento SOCKS. Estratta da `configure_proxy` il
    2026-08-25 perche' la pref di sicurezza qui sopra vive in UN posto solo e
    doveva stare dopo la validazione di ENTRAMBI gli schemi."""
    host_port = _strip_scheme(server)
    if ":" not in host_port:
        # It used to `return None  # malformed, drop silently`, and a test named
        # test_cp14_socks_without_port_dropped_silently pinned that. Changed
        # 2026-08-01 after reading what the silence costs: the caller asked for a
        # proxy, no network.proxy.* pref is written, and the session goes out on
        # the host's own address believing it is proxied. For this package that
        # is the worst outcome there is, and it is invisible - the browser
        # launches, the page loads, the IP is wrong.
        #
        # The other parser disagreed too: _geo builds `socks5h://host` from the
        # same dict and hands it to requests, so one half of a session was
        # proxied and the other was not.
        raise ValueError(
            f"proxy server {server!r} has no port. A SOCKS endpoint needs "
            f"host:port - e.g. socks5://host:1080. Refusing rather than "
            f"launching unproxied, which is what this used to do silently")

    host, port_str = host_port.rsplit(":", 1)
    prefs["network.proxy.type"]            = 1
    prefs["network.proxy.socks"]           = host
    prefs["network.proxy.socks_port"]      = int(port_str)
    prefs["network.proxy.socks_version"]   = 4 if server.lower().startswith("socks4://") else 5
    prefs["network.proxy.socks_username"]  = proxy.get("username") or ""
    prefs["network.proxy.socks_password"]  = proxy.get("password") or ""
    prefs["network.proxy.socks_remote_dns"] = True
    return None


def _configure_http_like(
    proxy: Dict[str, str],
    prefs: Dict[str, Any],
    server: str,
    delegates_auth: bool,
) -> Optional[Dict[str, str]]:
    """An HTTP/HTTPS endpoint, for a caller that may or may not have Playwright.

    Routing and authentication are two different problems here, and only the
    second one needs Playwright:

    * ROUTING is pure prefs, and it was measured working on the shipped binary:
      with ``network.proxy.type`` plus ``http``/``http_port``/``ssl``/``ssl_port``
      the browser goes to the proxy and does NOT fall back to direct.
    * AUTHENTICATION is not. The credentials we write for SOCKS reach the
      ``nsProxyInfo``, but nothing builds a ``Proxy-Authorization`` header out of
      them, so an authenticated endpoint stops at the 407 and Gecko reports
      ``NS_ERROR_PROXY_CONNECTION_REFUSED``. Playwright's own proxy support is
      what answers that challenge today.

    Hence: a caller that can delegate keeps the previous behaviour exactly, so
    the path measured green stays untouched. A caller that cannot gets the
    routing prefs, and a REFUSAL when credentials are present - because the one
    thing that must never happen again is launching unproxied while believing
    otherwise.
    """
    if delegates_auth:
        return proxy

    host_port = _strip_scheme(server)
    if ":" not in host_port:
        raise ValueError(
            f"proxy server {server!r} has no port. An HTTP endpoint needs "
            f"host:port - e.g. http://host:8080")
    host, port_str = host_port.rsplit(":", 1)

    if proxy.get("username") or proxy.get("password"):
        raise ValueError(
            f"proxy server {server!r} carries credentials, and this launch path "
            f"cannot deliver them: it starts the binary directly, so there is no "
            f"Playwright to answer the proxy's 407, and the browser has no pref "
            f"that injects Proxy-Authorization (only SOCKS has that). Use a SOCKS "
            f"endpoint here, or drive this proxy through invisible_playwright, "
            f"which does answer the challenge. Refusing rather than launching "
            f"unproxied, which is what this used to do silently")

    prefs["network.proxy.type"]      = 1
    prefs["network.proxy.http"]      = host
    prefs["network.proxy.http_port"] = int(port_str)
    prefs["network.proxy.ssl"]       = host
    prefs["network.proxy.ssl_port"]  = int(port_str)
    return None


#: Il browser fa passare l'UDP DENTRO il tunnel SOCKS?
#:
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


def _is_socks_scheme(server: str) -> bool:
    return server.lower().startswith(_SOCKS_SCHEMES)


def _strip_scheme(server: str) -> str:
    return server.split("://", 1)[1] if "://" in server else server
