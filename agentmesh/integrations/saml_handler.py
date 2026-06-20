"""
SAML / SSO Identity Propagation

Extracts verified identity from SAML assertions or OIDC JWT tokens
passed in request headers. Populates the AgentMesh QuotaIdentity
with org-verified team, user, and role — replacing header self-reporting.

Supported flows:
  1. SAML 2.0 Bearer Assertion  (X-AgentMesh-SAML-Assertion header)
  2. OIDC JWT Bearer Token      (Authorization: Bearer <jwt>)
  3. Pre-verified header        (X-AgentMesh-Verified-Team / User set by IdP proxy)

This module does NOT perform SAML SP validation (that requires xmlsec1
and a full IdP integration). Instead it:
  - Decodes JWT claims without signature verification (for internal proxies
    behind an IdP that already verified the token)
  - Provides hooks for plugging in full verification (e.g. python-saml, authlib)
  - Maps IdP claims → AgentMesh identity fields via a configurable claim map

For production: integrate with your IdP using python3-saml or authlib,
then call SSOIdentityExtractor.from_verified_claims(claims).

Usage:
  extractor = SSOIdentityExtractor(
      claim_map={"email": "user", "department": "team", "role": "tool"},
      trusted_issuers={"https://accounts.google.com"},
  )
  identity = extractor.extract(request_headers)
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class SSOIdentity:
    user:   str
    team:   str
    role:   str
    email:  str
    source: str   # "jwt" | "saml" | "header" | "fallback"
    raw_claims: dict


@dataclass
class SSOConfig:
    """
    Configuration for SSO/SAML identity extraction.

    claim_map: maps IdP claim names → AgentMesh fields (user, team, role)
    trusted_issuers: set of allowed JWT issuers (empty = skip issuer check)
    team_claim_values: map IdP team/dept values → AgentMesh team names
    require_sso: if True, reject requests without valid SSO identity
    """
    claim_map: Dict[str, str] = None  # type: ignore
    trusted_issuers: set = None       # type: ignore
    team_claim_values: Dict[str, str] = None  # type: ignore
    require_sso: bool = False

    def __post_init__(self):
        if self.claim_map is None:
            self.claim_map = {
                # JWT standard claims
                "sub":         "user",
                "email":       "email",
                "name":        "user",
                # Common IdP-specific claims
                "department":  "team",
                "groups":      "team",
                "team":        "team",
                "role":        "role",
                "roles":       "role",
                # Google Workspace
                "hd":          "team",
                # Okta / Azure AD
                "groups":      "team",
            }
        if self.trusted_issuers is None:
            self.trusted_issuers = set()
        if self.team_claim_values is None:
            self.team_claim_values = {}


def _decode_jwt_unverified(token: str) -> Optional[dict]:
    """
    Decode JWT payload without signature verification.
    For use behind an IdP proxy that already verified the token.
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        # Add padding
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception as e:
        logger.debug("JWT decode failed: %s", e)
        return None


def _decode_saml_assertion(assertion_b64: str) -> Optional[dict]:
    """
    Minimally parse a base64-encoded SAML assertion for attribute values.
    For production, use python3-saml for full validation.
    """
    try:
        import xml.etree.ElementTree as ET
        xml_bytes = base64.b64decode(assertion_b64)
        root = ET.fromstring(xml_bytes)
        ns = {
            "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
        }
        claims: dict = {}

        # Extract NameID as user
        name_id = root.find(".//saml:NameID", ns)
        if name_id is not None and name_id.text:
            claims["sub"] = name_id.text.strip()
            if "@" in claims["sub"]:
                claims["email"] = claims["sub"]

        # Extract Attribute values
        for attr in root.findall(".//saml:Attribute", ns):
            name = attr.get("Name", "").split("/")[-1].lower()
            values = [
                v.text.strip()
                for v in attr.findall("saml:AttributeValue", ns)
                if v.text
            ]
            if values:
                claims[name] = values[0] if len(values) == 1 else values

        return claims
    except Exception as e:
        logger.debug("SAML parse failed: %s", e)
        return None


class SSOIdentityExtractor:
    """
    Extract verified SSO/SAML identity from HTTP request headers.

    Falls back gracefully: SAML → JWT → Pre-verified headers → None
    """

    def __init__(self, config: Optional[SSOConfig] = None):
        self.cfg = config or SSOConfig()

    def extract(self, headers: dict) -> Optional[SSOIdentity]:
        """
        Try each extraction method in priority order.
        Returns None if no SSO identity found (caller uses header fallback).
        """
        # 1. SAML assertion
        saml = headers.get("X-AgentMesh-SAML-Assertion", "")
        if saml:
            claims = _decode_saml_assertion(saml)
            if claims:
                return self._map_claims(claims, source="saml")

        # 2. JWT Bearer
        auth = headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
            claims = _decode_jwt_unverified(token)
            if claims:
                iss = claims.get("iss", "")
                if self.cfg.trusted_issuers and iss not in self.cfg.trusted_issuers:
                    logger.warning("JWT issuer '%s' not in trusted list — skipping", iss)
                else:
                    return self._map_claims(claims, source="jwt")

        # 3. Pre-verified proxy headers (set by upstream IdP proxy/API gateway)
        verified_user = headers.get("X-AgentMesh-Verified-User", "")
        verified_team = headers.get("X-AgentMesh-Verified-Team", "")
        if verified_user or verified_team:
            return SSOIdentity(
                user=verified_user,
                team=verified_team or "default",
                role=headers.get("X-AgentMesh-Verified-Role", ""),
                email=verified_user if "@" in verified_user else "",
                source="header",
                raw_claims={},
            )

        if self.cfg.require_sso:
            logger.warning("SSO required but no identity found in request")

        return None

    def _map_claims(self, claims: dict, source: str) -> SSOIdentity:
        cmap = self.cfg.claim_map
        tmap = self.cfg.team_claim_values

        def _get(field: str) -> str:
            for claim_key, mapped_field in cmap.items():
                if mapped_field == field and claim_key in claims:
                    val = claims[claim_key]
                    if isinstance(val, list):
                        val = val[0]
                    return str(val)
            return ""

        user  = _get("user") or _get("email") or ""
        email = _get("email") or (user if "@" in user else "")
        team  = _get("team") or ""
        role  = _get("role") or ""

        # Apply team value mapping (e.g. "Engineering Dept" → "engineering")
        if team and tmap:
            team = tmap.get(team.lower(), team)

        team = team.lower().replace(" ", "-") if team else "default"

        return SSOIdentity(
            user=user, team=team, role=role, email=email,
            source=source, raw_claims=claims,
        )
