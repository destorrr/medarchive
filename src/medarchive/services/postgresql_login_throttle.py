"""PostgreSQL-backed login throttling shared by all application workers."""
import hashlib
import hmac
import ipaddress

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class PostgreSQLLoginThrottle:
    """Bound failed logins by client IP and by username/IP pair.

    Only keyed digests are stored; raw usernames and IP addresses never enter
    the throttle table. PostgreSQL upserts make counters atomic across workers.
    """

    WINDOW_MINUTES = 15
    BLOCK_MINUTES = 15
    IP_FAILURE_LIMIT = 20
    IDENTITY_FAILURE_LIMIT = 5

    def __init__(self, session: AsyncSession, secret_key: str):
        self.session = session
        self._secret_key = secret_key.encode("utf-8")

    def _digest(self, scope: str, value: str) -> str:
        message = f"{scope}:{value}".encode("utf-8")
        return hmac.new(self._secret_key, message, hashlib.sha256).hexdigest()

    def _keys(self, username: str, client_ip: str) -> tuple[str, str]:
        normalized_username = username.strip().casefold()
        normalized_ip = client_ip.strip() or "unknown"
        try:
            normalized_ip = ipaddress.ip_address(normalized_ip).compressed
        except ValueError:
            pass
        return (
            self._digest("ip", normalized_ip),
            self._digest("identity-ip", f"{normalized_username}\0{normalized_ip}"),
        )

    async def lock_buckets(self, username: str, client_ip: str) -> None:
        """Serialize checks and counter updates for the same IP/identity pair."""
        keys = self._keys(username, client_ip)
        lock_ids = []
        for key in keys:
            value = int(key[:16], 16)
            if value >= 2**63:
                value -= 2**64
            lock_ids.append(value)
        for lock_id in sorted(set(lock_ids)):
            await self.session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": lock_id},
            )

    async def retry_after(self, username: str, client_ip: str) -> int | None:
        ip_key, identity_key = self._keys(username, client_ip)
        result = await self.session.execute(
            text("""
                SELECT CEIL(EXTRACT(EPOCH FROM (MAX(blocked_until) - now())))::integer
                FROM auth_login_throttles
                WHERE throttle_key IN (:ip_key, :identity_key)
                  AND blocked_until > now()
            """),
            {"ip_key": ip_key, "identity_key": identity_key},
        )
        seconds = result.scalar_one_or_none()
        return max(1, int(seconds)) if seconds is not None else None

    async def record_failure(self, username: str, client_ip: str) -> None:
        ip_key, identity_key = self._keys(username, client_ip)
        for throttle_key, limit in (
            (ip_key, self.IP_FAILURE_LIMIT),
            (identity_key, self.IDENTITY_FAILURE_LIMIT),
        ):
            await self.session.execute(
                text("""
                    INSERT INTO auth_login_throttles (
                        throttle_key, failed_attempts, window_started_at,
                        blocked_until, updated_at
                    )
                    VALUES (:throttle_key, 1, now(), NULL, now())
                    ON CONFLICT (throttle_key) DO UPDATE SET
                        failed_attempts = CASE
                            WHEN auth_login_throttles.window_started_at
                                 <= now() - (:window_minutes * interval '1 minute')
                            THEN 1
                            ELSE auth_login_throttles.failed_attempts + 1
                        END,
                        window_started_at = CASE
                            WHEN auth_login_throttles.window_started_at
                                 <= now() - (:window_minutes * interval '1 minute')
                            THEN now()
                            ELSE auth_login_throttles.window_started_at
                        END,
                        blocked_until = CASE
                            WHEN (
                                CASE
                                    WHEN auth_login_throttles.window_started_at
                                         <= now() - (:window_minutes * interval '1 minute')
                                    THEN 1
                                    ELSE auth_login_throttles.failed_attempts + 1
                                END
                            ) >= :failure_limit
                            THEN now() + (:block_minutes * interval '1 minute')
                            ELSE auth_login_throttles.blocked_until
                        END,
                        updated_at = now()
                """),
                {
                    "throttle_key": throttle_key,
                    "window_minutes": self.WINDOW_MINUTES,
                    "block_minutes": self.BLOCK_MINUTES,
                    "failure_limit": limit,
                },
            )
        # Bound table growth: discard buckets that cannot affect a future decision.
        await self.session.execute(text("""
            DELETE FROM auth_login_throttles
            WHERE updated_at < now() - interval '1 day'
              AND (blocked_until IS NULL OR blocked_until <= now())
        """))

    async def clear_identity_failures(self, username: str, client_ip: str) -> None:
        _, identity_key = self._keys(username, client_ip)
        await self.session.execute(
            text("DELETE FROM auth_login_throttles WHERE throttle_key = :throttle_key"),
            {"throttle_key": identity_key},
        )
