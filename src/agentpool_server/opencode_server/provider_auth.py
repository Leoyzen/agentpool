"""Provider authentication service.

Composable auth backend system matching the opencode plugin auth pattern.
Each provider registers a backend that handles its specific auth flow
(OAuth PKCE, device code, API key, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
from llmling_models.auth.anthropic_auth import (
    OAUTH_MANUAL_REDIRECT_URI,
    AnthropicOAuthToken,
    AnthropicTokenStore,
    build_authorization_url,
    exchange_code_for_token,
    generate_pkce,
)
from llmling_models.auth.antigravity_auth import (
    AntigravityTokenStore,
    build_authorization_url as antigravity_build_auth_url,
    exchange_code_for_token as antigravity_exchange_code,
    generate_pkce as antigravity_generate_pkce,
)
from llmling_models.auth.gemini_auth import (
    GeminiTokenStore,
    build_authorization_url as gemini_build_auth_url,
    exchange_code_for_token as gemini_exchange_code,
    generate_pkce as gemini_generate_pkce,
)
from llmling_models.auth.github_auth import (
    CopilotTokenStore,
    refresh_copilot_token,
)
from llmling_models.auth.openai_codex_auth import (
    OpenAICodexTokenStore,
    build_authorization_url as codex_build_auth_url,
    exchange_code_for_token as codex_exchange_code,
    generate_pkce as codex_generate_pkce,
)

from opencode_sdk.models import OAuthAuthInfo, ProviderAuthAuthorization, ProviderAuthMethod


if TYPE_CHECKING:
    from opencode_sdk.models import AuthInfo


class ProviderAuthBackend(ABC):
    """Protocol for a provider-specific auth backend."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Unique provider identifier."""
        ...

    @abstractmethod
    def methods(self) -> list[ProviderAuthMethod]:
        """Return available auth methods for this provider."""
        ...

    @abstractmethod
    async def authorize(self, method: int = 0) -> ProviderAuthAuthorization:
        """Start an authorization flow.

        Args:
            method: Index into the methods list.

        Returns:
            Authorization info with URL and instructions.
        """
        ...

    @abstractmethod
    async def callback(
        self,
        *,
        code: str | None = None,
        device_code: str | None = None,
        verifier: str | None = None,
    ) -> bool:
        """Handle the auth callback / code exchange.

        Returns:
            True if auth succeeded.

        Raises:
            ValueError: If required parameters are missing or exchange fails.
        """
        ...

    async def set_credentials(self, info: AuthInfo) -> bool:
        """Store credentials for this provider.

        Default implementation is a no-op. Override for providers that
        support direct credential setting (e.g. API key or token import).
        """
        return False

    async def remove_credentials(self) -> bool:
        """Remove stored credentials for this provider.

        Default implementation is a no-op.
        """
        return False


class AnthropicAuthBackend(ProviderAuthBackend):
    """Anthropic OAuth (PKCE) auth backend."""

    def __init__(self) -> None:
        self._pending_verifiers: dict[str, str] = {}

    @property
    def provider_id(self) -> str:
        return "anthropic"

    def methods(self) -> list[ProviderAuthMethod]:
        return [ProviderAuthMethod(type="oauth", label="Connect Claude Max/Pro")]

    async def authorize(self, method: int = 0) -> ProviderAuthAuthorization:
        verifier, challenge = generate_pkce()
        auth_url = build_authorization_url(verifier, challenge, OAUTH_MANUAL_REDIRECT_URI)
        self._pending_verifiers[verifier] = verifier
        return ProviderAuthAuthorization(
            url=auth_url,
            instructions="Sign in with your Anthropic account and copy the authorization code",
            method="code",
        )

    async def callback(
        self,
        *,
        code: str | None = None,
        device_code: str | None = None,
        verifier: str | None = None,
    ) -> bool:
        if not code or not verifier:
            raise ValueError("Missing code or verifier for Anthropic OAuth")
        token = exchange_code_for_token(code, verifier, verifier, OAUTH_MANUAL_REDIRECT_URI)
        store = AnthropicTokenStore()
        store.save(token)
        self._pending_verifiers.pop(verifier, None)
        return True

    async def set_credentials(self, info: AuthInfo) -> bool:
        if not isinstance(info, OAuthAuthInfo):
            return False
        store = AnthropicTokenStore()
        token = AnthropicOAuthToken(
            access_token=info.access,
            refresh_token=info.refresh,
            expires_at=info.expires,
        )
        store.save(token)
        return True

    async def remove_credentials(self) -> bool:
        store = AnthropicTokenStore()
        store.clear()
        return True


COPILOT_CLIENT_ID = "Iv1.b507a08c87ecfe98"
COPILOT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "GitHubCopilotChat/0.35.0",
}


class CopilotAuthBackend(ProviderAuthBackend):
    """GitHub Copilot device-code auth backend."""

    def __init__(self) -> None:
        self._pending_device_codes: dict[str, str] = {}

    @property
    def provider_id(self) -> str:
        return "copilot"

    def methods(self) -> list[ProviderAuthMethod]:
        return [ProviderAuthMethod(type="oauth", label="Connect GitHub Copilot")]

    async def authorize(self, method: int = 0) -> ProviderAuthAuthorization:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://github.com/login/device/code",
                headers=COPILOT_HEADERS,
                json={"client_id": COPILOT_CLIENT_ID, "scope": "read:user"},
            )
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()

        device_code = data["device_code"]
        user_code = data["user_code"]
        verification_uri = data["verification_uri"]
        self._pending_device_codes[device_code] = device_code

        return ProviderAuthAuthorization(
            url=verification_uri,
            instructions=f"Enter code: {user_code}",
            method="auto",
        )

    async def callback(
        self,
        *,
        code: str | None = None,
        device_code: str | None = None,
        verifier: str | None = None,
    ) -> bool:
        if not device_code:
            msg = "Missing device_code for Copilot OAuth"
            raise ValueError(msg)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://github.com/login/oauth/access_token",
                headers=COPILOT_HEADERS,
                data={
                    "client_id": COPILOT_CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
            )
            data: dict[str, Any] = resp.json()

        if "error" in data:
            detail = data.get("error_description", data["error"])
            raise ValueError(detail)

        github_token = data.get("access_token")
        if not github_token:
            raise ValueError("No token received")

        self._pending_device_codes.pop(device_code, None)

        # Exchange GitHub token for Copilot API token and store
        copilot_data = refresh_copilot_token(github_token)
        store = CopilotTokenStore()
        store.save({
            "github_token": github_token,
            "copilot_token": copilot_data["copilot_token"],
            "base_url": copilot_data["base_url"],
            "expires_at": copilot_data["expires_at"],
            "enterprise_domain": None,
        })
        return True


@dataclass
class ProviderAuthService:
    """Registry of provider auth backends.

    Mirrors opencode's ProviderAuth namespace — routes call service methods
    instead of containing provider-specific logic.
    """

    _backends: dict[str, ProviderAuthBackend] = field(default_factory=dict)

    def register(self, backend: ProviderAuthBackend) -> None:
        """Register an auth backend."""
        self._backends[backend.provider_id] = backend

    def get_backend(self, provider_id: str) -> ProviderAuthBackend:
        """Get backend by provider ID.

        Raises:
            KeyError: If provider_id is not registered.
        """
        try:
            return self._backends[provider_id]
        except KeyError:
            msg = f"Unknown provider: {provider_id}"
            raise KeyError(msg) from None

    def methods(self) -> dict[str, list[ProviderAuthMethod]]:
        """Return auth methods for all registered providers."""
        return {pid: backend.methods() for pid, backend in self._backends.items()}

    async def authorize(self, provider_id: str, method: int = 0) -> ProviderAuthAuthorization:
        """Start auth flow for a provider."""
        return await self.get_backend(provider_id).authorize(method)

    async def callback(
        self,
        provider_id: str,
        *,
        code: str | None = None,
        device_code: str | None = None,
        verifier: str | None = None,
    ) -> bool:
        """Handle auth callback for a provider."""
        return await self.get_backend(provider_id).callback(
            code=code, device_code=device_code, verifier=verifier
        )

    async def set_credentials(self, provider_id: str, info: AuthInfo) -> bool:
        """Set credentials for a provider."""
        return await self.get_backend(provider_id).set_credentials(info)

    async def remove_credentials(self, provider_id: str) -> bool:
        """Remove credentials for a provider."""
        return await self.get_backend(provider_id).remove_credentials()


class GeminiAuthBackend(ProviderAuthBackend):
    """Google Gemini CLI (Cloud Code Assist) OAuth backend."""

    def __init__(self) -> None:
        self._pending_verifiers: dict[str, str] = {}

    @property
    def provider_id(self) -> str:
        return "gemini"

    def methods(self) -> list[ProviderAuthMethod]:
        return [ProviderAuthMethod(type="oauth", label="Connect Gemini CLI")]

    async def authorize(self, method: int = 0) -> ProviderAuthAuthorization:
        verifier, challenge = gemini_generate_pkce()
        auth_url = gemini_build_auth_url(verifier, challenge)
        self._pending_verifiers[verifier] = verifier
        return ProviderAuthAuthorization(
            url=auth_url,
            instructions="Sign in with your Google account",
            method="auto",
        )

    async def callback(
        self,
        *,
        code: str | None = None,
        device_code: str | None = None,
        verifier: str | None = None,
    ) -> bool:
        if not code or not verifier:
            msg = "Missing code or verifier for Gemini OAuth"
            raise ValueError(msg)
        gemini_exchange_code(code, verifier)
        # Note: project discovery requires a separate step;
        # for server-side flows the project_id should be set via credentials.
        self._pending_verifiers.pop(verifier, None)
        return True

    async def remove_credentials(self) -> bool:
        GeminiTokenStore().clear()
        return True


class AntigravityAuthBackend(ProviderAuthBackend):
    """Antigravity (Gemini 3, Claude, GPT-OSS via Google Cloud) OAuth backend."""

    def __init__(self) -> None:
        self._pending_verifiers: dict[str, str] = {}

    @property
    def provider_id(self) -> str:
        return "antigravity"

    def methods(self) -> list[ProviderAuthMethod]:
        return [ProviderAuthMethod(type="oauth", label="Connect Antigravity")]

    async def authorize(self, method: int = 0) -> ProviderAuthAuthorization:
        verifier, challenge = antigravity_generate_pkce()
        auth_url = antigravity_build_auth_url(verifier, challenge)
        self._pending_verifiers[verifier] = verifier
        return ProviderAuthAuthorization(
            url=auth_url,
            instructions="Sign in with your Google account",
            method="auto",
        )

    async def callback(
        self,
        *,
        code: str | None = None,
        device_code: str | None = None,
        verifier: str | None = None,
    ) -> bool:
        if not code or not verifier:
            msg = "Missing code or verifier for Antigravity OAuth"
            raise ValueError(msg)
        antigravity_exchange_code(code, verifier)
        self._pending_verifiers.pop(verifier, None)
        return True

    async def remove_credentials(self) -> bool:
        AntigravityTokenStore().clear()
        return True


class OpenAICodexAuthBackend(ProviderAuthBackend):
    """OpenAI Codex (ChatGPT Plus/Pro) OAuth backend."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[str, str]] = {}  # state -> (verifier, state)

    @property
    def provider_id(self) -> str:
        return "openai-codex"

    def methods(self) -> list[ProviderAuthMethod]:
        return [ProviderAuthMethod(type="oauth", label="Connect ChatGPT Plus/Pro")]

    async def authorize(self, method: int = 0) -> ProviderAuthAuthorization:
        import secrets as _secrets

        verifier, challenge = codex_generate_pkce()
        state = _secrets.token_hex(16)
        auth_url = codex_build_auth_url(verifier, challenge, state)
        self._pending[state] = (verifier, state)
        return ProviderAuthAuthorization(
            url=auth_url,
            instructions="Sign in with your OpenAI account",
            method="auto",
        )

    async def callback(
        self,
        *,
        code: str | None = None,
        device_code: str | None = None,
        verifier: str | None = None,
    ) -> bool:
        if not code or not verifier:
            msg = "Missing code or verifier for OpenAI Codex OAuth"
            raise ValueError(msg)
        # verifier here is the PKCE verifier stored during authorize
        token = codex_exchange_code(code, verifier)
        store = OpenAICodexTokenStore()
        store.save(token)
        return True

    async def remove_credentials(self) -> bool:
        OpenAICodexTokenStore().clear()
        return True


def create_default_auth_service() -> ProviderAuthService:
    """Create auth service with built-in providers."""
    service = ProviderAuthService()
    service.register(AnthropicAuthBackend())
    service.register(CopilotAuthBackend())
    service.register(GeminiAuthBackend())
    service.register(AntigravityAuthBackend())
    service.register(OpenAICodexAuthBackend())
    return service
