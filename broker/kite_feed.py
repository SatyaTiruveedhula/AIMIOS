import logging
import os
import sqlite3
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

from dotenv import load_dotenv
from kiteconnect import KiteConnect

from broker.broker_base import BrokerBase

PROJECT_ROOT = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)


class KiteAuthError(Exception):
    pass


class RequestTokenHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        logger.info("Callback request received: %s", self.path)
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        request_token = params.get("request_token", [None])[0]
        error = params.get("error", [None])[0]

        if (
            parsed.path != getattr(self.server, "callback_path", "/callback")
            and not request_token
            and not error
        ):
            self.send_error(404, "Not Found")
            return

        if request_token:
            self.server.request_token = request_token
            self.server.error = None
            body = (
                "<html><body><h1>Authentication successful.</h1>"
                "<p>You can close this browser window.</p></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())
            self.server.request_token_event.set()
            return

        if error:
            self.server.request_token = None
            self.server.error = error
            body = (
                "<html><body><h1>Authentication failed.</h1>"
                f"<p>{error}</p></body></html>"
            )
            self.send_response(400)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body.encode())
            self.server.request_token_event.set()
            return

        self.send_error(400, "Missing request token")

    def log_message(self, format: str, *args) -> None:
        logger.debug("Callback server: %s", format % args)


class KiteAuthStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = (
            db_path or Path(__file__).resolve().parent.parent / "database" / "aimios.db"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.db_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS kite_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                access_token TEXT NOT NULL,
                public_token TEXT,
                user_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """)
        self.connection.commit()

    def save_access_token(
        self,
        access_token: str,
        public_token: str | None,
        user_id: str | None,
        created_at: datetime,
    ) -> int:
        cursor = self.connection.cursor()
        cursor.execute(
            "INSERT INTO kite_sessions (access_token, public_token, user_id, created_at) VALUES (?, ?, ?, ?)",
            (access_token, public_token, user_id, created_at.isoformat()),
        )
        self.connection.commit()
        return cursor.lastrowid

    def close(self) -> None:
        self.connection.close()

    def get_latest_access_token(self) -> dict | None:
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT access_token, public_token, user_id, created_at FROM kite_sessions ORDER BY id DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_access_tokens(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM kite_sessions")
        self.connection.commit()
        logger.info("Deleted cached Kite sessions")


class KiteFeed(BrokerBase):
    def __init__(self) -> None:
        self.env_path = PROJECT_ROOT / ".env"
        self._load_environment()
        self._validate_configuration()

        self.client: KiteConnect | None = None
        self.callback_server: HTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self.request_token: str | None = None
        self.access_token: str | None = None
        self.connected = False
        self.logged_in = False
        self.broker_name = "KITE"
        self.storage = KiteAuthStore(PROJECT_ROOT / "database" / "aimios.db")

    def _load_environment(self) -> None:
        if not self.env_path.exists():
            raise KiteAuthError(f"Environment file not found: {self.env_path}")

        load_dotenv(dotenv_path=self.env_path)
        self.api_key = os.getenv("KITE_API_KEY")
        self.api_secret = os.getenv("KITE_API_SECRET")
        self.redirect_url = os.getenv("REDIRECT_URL")
        self.callback_port = os.getenv("CALLBACK_PORT")
        logger.info("Loaded environment values from %s", self.env_path)

    def _validate_configuration(self) -> None:
        missing = []
        if not self.api_key:
            missing.append("KITE_API_KEY")
        if not self.api_secret:
            missing.append("KITE_API_SECRET")
        if not self.redirect_url:
            missing.append("REDIRECT_URL")
        if missing:
            raise KiteAuthError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        parsed = urlparse(self.redirect_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise KiteAuthError(
                f"REDIRECT_URL must be a valid HTTP/HTTPS URL: {self.redirect_url}"
            )

        self.callback_host = parsed.hostname
        self.callback_port = parsed.port or int(self.callback_port or 8000)
        self.callback_path = parsed.path or "/callback"
        if not self.callback_path.startswith("/"):
            self.callback_path = "/" + self.callback_path

        logger.info(
            "Using callback listener on http://%s:%s%s",
            self.callback_host,
            self.callback_port,
            self.callback_path,
        )

    def connect(self) -> None:
        logger.info("Entering KiteFeed.connect()")
        logger.info("Initializing KiteConnect client")
        self.client = KiteConnect(api_key=self.api_key)
        self.connected = True
        logger.info("KiteConnect client initialized")

    def _load_cached_session(self) -> bool:
        cached = self.storage.get_latest_access_token()

        if not cached:
            logger.info("No cached Kite access token found")
            return False

        self.access_token = cached["access_token"]

        if self.client is None:
            logger.error("Kite client is not initialized")
            return False

        try:
            # Apply cached token
            self.client.set_access_token(self.access_token)

            # Validate token with Kite
            profile = self.client.profile()

            logger.info(
                "Cached Kite access token is valid for user: %s",
                profile.get("user_name", profile.get("user_id", "Unknown")),
            )

            self.logged_in = True
            return True

        except Exception as exc:
            logger.warning("Cached Kite access token has expired or is invalid")
            logger.warning("Reason: %s", exc)

            self.access_token = None
            self.logged_in = False

            try:
                self.storage.delete_access_tokens()
                logger.info("Expired cached token removed from storage")
            except Exception:
                logger.warning("Could not remove expired token from storage")

            return False

    def login(self, timeout: int = 180) -> str | None:
        logger.info("Entering KiteFeed.login()")
        if self.client is None:
            raise KiteAuthError("Kite client is not initialized. Call connect() first.")

        if self.access_token and self.logged_in:
            logger.info("Already authenticated with cached Kite token")
            return self.access_token

        if self._load_cached_session():
            return self.access_token

        self._start_callback_listener()

        if not self.callback_server:
            raise KiteAuthError("Callback server failed to initialize")

        if self.redirect_url:
            redirect_param = quote_plus(self.redirect_url)
            login_url = f"{self.client._default_login_uri}?api_key={self.api_key}&v={self.client.kite_header_version}&redirect_url={redirect_param}"
        else:
            login_url = self.client.login_url()

        logger.info("Generated login URL")
        logger.info("Opening login URL in browser: %s", login_url)
        try:
            webbrowser.open(login_url)
        except (OSError, webbrowser.Error) as exc:
            logger.exception("Browser open failed")
            logger.warning("Unable to open browser automatically: %s", exc)
            logger.info("Please open the following URL manually: %s", login_url)

        logger.info("Browser authentication expected; waiting for OAuth callback")
        logger.info("Waiting for request token on %s", self.redirect_url)
        completed = self.callback_server.request_token_event.wait(timeout=timeout)

        request_token = getattr(self.callback_server, "request_token", None)
        error = getattr(self.callback_server, "error", None)
        self._stop_callback_listener()

        if not completed:
            raise KiteAuthError("Login timeout waiting for request token")

        if error:
            raise KiteAuthError(f"Kite login failed: {error}")

        self.request_token = request_token
        logger.info(
            "Request token extracted from callback: %s",
            "present" if bool(self.request_token) else "missing",
        )
        if not self.request_token:
            raise KiteAuthError("Request token was not received")

        logger.info("Captured request token successfully")
        return self.request_token

    def generate_session(self) -> str:
        if self.client is None:
            raise KiteAuthError("Kite client is not initialized. Call connect() first.")
        if not self.request_token:
            raise KiteAuthError("Request token missing. Call login() first.")

        try:
            logger.info("Generating Kite session from request token")
            session_data = self.client.generate_session(
                self.request_token, api_secret=self.api_secret
            )
        except Exception as exc:
            logger.exception("Failed to generate Kite session")
            logger.error("Failed to generate Kite session: %s", exc)
            raise KiteAuthError("Failed to generate Kite session") from exc

        logger.info("Kite session generated successfully")
        self.access_token = session_data.get("access_token")
        public_token = session_data.get("public_token")
        user_id = session_data.get("user_id")
        created_at = datetime.now(timezone.utc)

        if not self.access_token:
            raise KiteAuthError(
                "Kite session generation did not return an access token"
            )

        self.client.set_access_token(self.access_token)
        logger.info("Set Kite access token on KiteConnect client")
        self.storage.save_access_token(
            self.access_token,
            public_token,
            user_id,
            created_at,
        )
        self.logged_in = True
        logger.info("Access token generated and stored securely")
        return self.access_token

    def disconnect(self) -> None:
        logger.info("Disconnecting Kite session")
        self._stop_callback_listener()
        self.client = None
        self.request_token = None
        self.access_token = None
        self.logged_in = False
        self.connected = False
        self.storage.close()
        logger.info("Disconnected")

    def get_quote(self, instrument: str):
        if self.client is None:
            raise KiteAuthError("Kite client is not initialized. Call connect() first.")
        if not self.access_token or not self.logged_in:
            raise KiteAuthError(
                "Kite client is not authenticated. Call generate_session() first."
            )

        logger.info("Fetching quote for %s", instrument)
        try:
            return self.client.ltp(instrument)
        except Exception as exc:
            logger.exception("Failed to fetch quote for %s", instrument)
            logger.error("Failed to fetch quote for %s: %s", instrument, exc)
            raise

    def get_quotes(self, instruments: list[str]):
        if self.client is None:
            raise KiteAuthError("Kite client is not initialized. Call connect() first.")
        if not self.access_token or not self.logged_in:
            raise KiteAuthError(
                "Kite client is not authenticated. Call generate_session() first."
            )

        logger.info("Fetching quotes for %s", instruments)
        try:
            return self.client.ltp(instruments)
        except Exception as exc:
            logger.exception("Failed to fetch quotes for %s", instruments)
            logger.error("Failed to fetch quotes for %s: %s", instruments, exc)
            raise

    def get_historical(
        self, instrument: str, start_date: datetime, end_date: datetime, interval: str
    ):
        if self.client is None:
            raise KiteAuthError("Kite client is not initialized. Call connect() first.")
        if not self.access_token or not self.logged_in:
            raise KiteAuthError(
                "Kite client is not authenticated. Call generate_session() first."
            )

        logger.info("Fetching historical data for %s", instrument)
        try:
            return self.client.historical_data(
                instrument, start_date, end_date, interval
            )
        except Exception as exc:
            logger.exception("Failed to fetch historical data for %s", instrument)
            logger.error("Failed to fetch historical data for %s: %s", instrument, exc)
            raise

    def start_websocket(self) -> None:
        logger.info("WebSocket mode not enabled yet.")

    def _start_callback_listener(self) -> None:
        if self.callback_server is not None:
            return

        host = "127.0.0.1"
        server_address = (host, self.callback_port)
        self.callback_server = HTTPServer(server_address, RequestTokenHandler)
        self.callback_server.request_token = None
        self.callback_server.error = None
        self.callback_server.request_token_event = threading.Event()
        self.callback_server.callback_path = self.callback_path
        self.server_thread = threading.Thread(
            target=self.callback_server.serve_forever, daemon=True
        )
        self.server_thread.start()
        logger.info(
            "Started callback server on http://%s:%s/callback",
            host,
            self.callback_port,
        )

    def _stop_callback_listener(self) -> None:
        if self.callback_server is None:
            return

        try:
            self.callback_server.shutdown()
            self.callback_server.server_close()
            logger.info("Stopped callback server")
        finally:
            self.callback_server = None
            self.server_thread = None
