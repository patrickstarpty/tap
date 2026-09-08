from authorization_policy_conformance import AuthorizationPolicyConformance, IdentityRegistry

from tap.modules.access.adapters.validation import ValidationAuthorizationPolicy
from tap.modules.access.application.ports import AuthorizationPolicy


class TestValidationIdentityPolicy(AuthorizationPolicyConformance):
    def make_policy(self, registry: IdentityRegistry) -> AuthorizationPolicy:
        return ValidationAuthorizationPolicy(registry)
