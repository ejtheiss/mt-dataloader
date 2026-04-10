"""MT resource configuration models — layers 0–1 (connections + foundation).

See ``resources_mid`` and ``resources_tail`` for later dependency layers.
"""

from __future__ import annotations

import base64 as _b64
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.shared import (
    AddressConfig,
    DisplayPhase,
    MetadataMixin,
    RefStr,
    _BaseResourceConfig,
)

# ---------------------------------------------------------------------------
# Layer 0 — Connections (sandbox-only creation via POST /connections)
# ---------------------------------------------------------------------------


class ConnectionConfig(_BaseResourceConfig):
    """Sandbox connection creation. NO MetadataMixin — the POST /connections
    endpoint only accepts ``entity_id`` and ``nickname``."""

    display_phase: ClassVar[int] = DisplayPhase.SETUP
    resource_type: ClassVar[str] = "connection"

    entity_id: Literal["example1", "example2", "modern_treasury"]
    nickname: str | None = None


# ---------------------------------------------------------------------------
# Layer 1 — Foundation resources (no inter-dependencies)
# ---------------------------------------------------------------------------


class DocumentConfig(BaseModel):
    """Inline document attached to a legal entity or identification."""

    model_config = ConfigDict(extra="forbid")

    document_type: Literal[
        "articles_of_incorporation",
        "certificate_of_good_standing",
        "ein_letter",
        "identification_back",
        "identification_front",
        "proof_of_address",
    ]
    file_data: str
    filename: str | None = None

    @model_validator(mode="after")
    def _default_filename(self) -> DocumentConfig:
        """Ensure filename is always present -- the MT API requires it."""
        if self.filename is None:
            self.filename = f"{self.document_type}.pdf"
        return self


class IdentificationConfig(BaseModel):
    """Legal entity identification (EIN, SSN, passport, etc.)."""

    model_config = ConfigDict(extra="forbid")

    id_number: str
    id_type: Literal[
        "ar_cuil",
        "ar_cuit",
        "br_cnpj",
        "br_cpf",
        "ca_sin",
        "cl_run",
        "cl_rut",
        "co_cedulas",
        "co_nit",
        "drivers_license",
        "hn_id",
        "hn_rtn",
        "in_lei",
        "kr_brn",
        "kr_crn",
        "kr_rrn",
        "passport",
        "sa_tin",
        "sa_vat",
        "us_ein",
        "us_itin",
        "us_ssn",
        "vn_tin",
    ]
    issuing_country: str | None = None
    documents: list[DocumentConfig] | None = None


_MOCK_PDF_B64: str = _b64.standard_b64encode(
    b"%PDF-1.0\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000108 00000 n \n"
    b"trailer<</Root 1 0 R/Size 4>>\n"
    b"startxref\n178\n%%EOF"
).decode()


def _mock_nine_digits(seed: str, offset: int = 0) -> str:
    """Deterministic 9-digit number from a seed string. Never starts with 0."""
    h = hash(seed) + offset
    n = abs(h) % 900_000_000 + 100_000_000
    return str(n)


class PhoneNumberConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone_number: str


class WealthAndEmploymentDetailsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    annual_income: int | None = None
    source_of_funds: (
        Literal[
            "alimony",
            "annuity",
            "business_owner",
            "business_revenue",
            "debt_financing",
            "general_employee",
            "government_benefits",
            "homemaker",
            "inheritance_gift",
            "intercompany_loan",
            "investment",
            "investor_funding",
            "legal_settlement",
            "lottery",
            "real_estate",
            "retained_earnings_or_savings",
            "retired",
            "retirement",
            "salary",
            "sale_of_business_assets",
            "sale_of_real_estate",
            "self_employed",
            "senior_executive",
            "trust_income",
        ]
        | None
    ) = None
    wealth_source: (
        Literal[
            "business_sale",
            "family_support",
            "government_benefits",
            "inheritance",
            "investments",
            "other",
            "rental_income",
            "retirement",
            "salary",
            "self_employed",
        ]
        | None
    ) = None
    occupation: (
        Literal[
            "consulting",
            "executive",
            "finance_accounting",
            "food_services",
            "government",
            "healthcare",
            "legal_services",
            "manufacturing",
            "other",
            "sales",
            "science_engineering",
            "technology",
        ]
        | None
    ) = None
    employment_status: (
        Literal[
            "employed",
            "retired",
            "self_employed",
            "student",
            "unemployed",
        ]
        | None
    ) = None
    employer_name: str | None = None
    employer_country: str | None = None
    income_source: (
        Literal[
            "family_support",
            "government_benefits",
            "inheritance",
            "investments",
            "rental_income",
            "retirement",
            "salary",
            "self_employed",
        ]
        | None
    ) = None
    industry: (
        Literal[
            "accounting",
            "agriculture",
            "automotive",
            "chemical_manufacturing",
            "construction",
            "educational_medical",
            "food_service",
            "finance",
            "gasoline",
            "health_stores",
            "laundry",
            "maintenance",
            "manufacturing",
            "merchant_wholesale",
            "mining",
            "performing_arts",
            "professional_non_legal",
            "public_administration",
            "publishing",
            "real_estate",
            "recreation_gambling",
            "religious_charity",
            "rental_services",
            "retail_clothing",
            "retail_electronics",
            "retail_food",
            "retail_furnishing",
            "retail_home",
            "retail_non_store",
            "retail_sporting",
            "transportation",
            "travel",
            "utilities",
        ]
        | None
    ) = None


class LegalEntityConfig(MetadataMixin, _BaseResourceConfig):
    """Legal entity config with **automatic mock data** for sandbox demos.

    For a business, provide only ``legal_entity_type`` and ``business_name``.
    For an individual, provide ``legal_entity_type``, ``first_name``, and
    ``last_name``.  The model validator fills in all remaining KYB/KYC fields
    (address, identifications, dates, documents, phone, wealth details) with
    compliant mock values so the MT sandbox accepts them.

    Any field you *do* set explicitly is kept as-is (except compliance
    fields which are always overwritten).
    """

    display_phase: ClassVar[int] = DisplayPhase.SETUP
    resource_type: ClassVar[str] = "legal_entity"

    legal_entity_type: Literal["business", "individual"]

    # Individual fields
    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    date_of_birth: str | None = None
    citizenship_country: str | None = None
    email: str | None = None

    # Business fields
    business_name: str | None = None
    date_formed: str | None = None
    legal_structure: (
        Literal[
            "corporation",
            "llc",
            "non_profit",
            "partnership",
            "sole_proprietorship",
            "trust",
        ]
        | None
    ) = None
    country_of_incorporation: str | None = None
    website: str | None = None

    # Shared
    addresses: list[AddressConfig] | None = None
    identifications: list[IdentificationConfig] | None = None
    documents: list[DocumentConfig] | None = None
    phone_numbers: list[PhoneNumberConfig] | None = None
    operating_jurisdictions: list[str] | None = None
    intended_use: str | None = None
    expected_activity_volume: int | None = None
    wealth_and_employment_details: WealthAndEmploymentDetailsConfig | None = None

    connection_id: RefStr | None = Field(
        default=None,
        description=(
            "MT legal-entity create ``connection_id`` (Connection Legal Entity). "
            "PSP with a **single** ``modern_treasury`` connection: omit — the field "
            "is dropped and not sent. Multiple connections: omit in JSON and the "
            "executor injects the UUID (fiat IA rail preferred). BYOB: set when "
            "your scenario requires it; otherwise omit."
        ),
    )

    @model_validator(mode="after")
    def _fill_mock_compliance_data(self) -> LegalEntityConfig:
        """Always overwrite compliance fields with sandbox-safe mock data.

        Identifications, addresses, documents, phone numbers, and
        wealth/employment details are fully managed by the mock -- any
        values the JSON provides are silently replaced.

        Documents live at two levels:
        - Entity-level ``documents`` (articles_of_incorporation, proof_of_address)
        - Nested on ``identifications[].documents`` (ein_letter, identification_front)
        """
        seed = self.ref

        if self.phone_numbers is None:
            self.phone_numbers = [PhoneNumberConfig(phone_number="+15551234567")]
        if self.intended_use is None:
            self.intended_use = "Sandbox demo and testing"
        if self.expected_activity_volume is None:
            self.expected_activity_volume = 100

        if self.legal_entity_type == "business":
            if self.date_formed is None:
                self.date_formed = "2020-01-15"
            if self.legal_structure is None:
                self.legal_structure = "llc"
            if self.country_of_incorporation is None:
                self.country_of_incorporation = "US"
            if self.email is None:
                self.email = "compliance@example.com"
            if self.operating_jurisdictions is None:
                self.operating_jurisdictions = ["US"]
            if self.wealth_and_employment_details is None:
                self.wealth_and_employment_details = WealthAndEmploymentDetailsConfig(
                    source_of_funds="business_revenue",
                    industry="finance",
                )
            self.identifications = [
                IdentificationConfig(
                    id_number=_mock_nine_digits(seed),
                    id_type="us_ein",
                    documents=[
                        DocumentConfig(
                            document_type="ein_letter",
                            file_data=_MOCK_PDF_B64,
                        ),
                    ],
                )
            ]
            self.addresses = [
                AddressConfig(
                    address_types=["business"],
                    line1="100 Main Street",
                    locality="New York",
                    region="NY",
                    postal_code="10001",
                    country="US",
                )
            ]
            self.documents = [
                DocumentConfig(
                    document_type="articles_of_incorporation",
                    file_data=_MOCK_PDF_B64,
                ),
                DocumentConfig(
                    document_type="proof_of_address",
                    file_data=_MOCK_PDF_B64,
                ),
            ]

        elif self.legal_entity_type == "individual":
            if self.date_of_birth is None:
                self.date_of_birth = "1990-06-15"
            if self.citizenship_country is None:
                self.citizenship_country = "US"
            if self.email is None:
                self.email = "individual@example.com"
            if self.middle_name is None:
                self.middle_name = "M"
            if self.wealth_and_employment_details is None:
                self.wealth_and_employment_details = WealthAndEmploymentDetailsConfig(
                    annual_income=100000,
                    wealth_source="salary",
                    occupation="technology",
                    employment_status="employed",
                    income_source="salary",
                    source_of_funds="salary",
                )
            self.identifications = [
                IdentificationConfig(
                    id_number=_mock_nine_digits(seed, offset=1),
                    id_type="us_ssn",
                ),
                IdentificationConfig(
                    id_number="A" + _mock_nine_digits(seed, offset=2)[:8],
                    id_type="passport",
                    issuing_country="US",
                    documents=[
                        DocumentConfig(
                            document_type="identification_front",
                            file_data=_MOCK_PDF_B64,
                        ),
                    ],
                ),
            ]
            self.addresses = [
                AddressConfig(
                    address_types=["residential"],
                    line1="200 Oak Avenue",
                    locality="Austin",
                    region="TX",
                    postal_code="73301",
                    country="US",
                )
            ]
            self.documents = None

        return self


class LedgerConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.SETUP
    resource_type: ClassVar[str] = "ledger"

    name: str
    description: str | None = None
