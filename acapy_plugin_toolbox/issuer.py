"""Define messages for credential issuer admin protocols."""
import asyncio

from uuid import uuid4

from marshmallow import fields

from aries_cloudagent.config.injection_context import InjectionContext
from aries_cloudagent.core.protocol_registry import ProtocolRegistry
from aries_cloudagent.messaging.base_handler import (
    BaseHandler,
    BaseResponder,
    RequestContext,
)
from aries_cloudagent.messaging.decorators.attach_decorator import AttachDecorator
from aries_cloudagent.messaging.credential_definitions.util import CRED_DEF_TAGS
from aries_cloudagent.protocols.issue_credential.v1_0.routes import (
    V10CredentialExchangeListResultSchema,
    V10CredentialProposalRequestMandSchema,
)
from aries_cloudagent.protocols.issue_credential.v1_0.models.credential_exchange import (
    V10CredentialExchange,
    V10CredentialExchangeSchema,
)
from aries_cloudagent.protocols.issue_credential.v1_0.messages.credential_proposal import (
    CredentialProposal,
)
from aries_cloudagent.protocols.issue_credential.v1_0.messages.inner.credential_preview import (
    CredentialPreview,
    CredentialPreviewSchema,
)
from aries_cloudagent.protocols.present_proof.v1_0.routes import (
    V10PresentationExchangeListSchema,
    IndyProofRequestSchema,
)
from aries_cloudagent.protocols.present_proof.v1_0.models.presentation_exchange import (
    V10PresentationExchange,
    V10PresentationExchangeSchema,
)
from aries_cloudagent.protocols.present_proof.v1_0.messages.presentation_request import (
    PresentationRequest,
)
from aries_cloudagent.protocols.present_proof.v1_0.manager import PresentationManager
from aries_cloudagent.protocols.issue_credential.v1_0.manager import CredentialManager
from aries_cloudagent.connections.models.connection_record import ConnectionRecord
from aries_cloudagent.storage.error import StorageNotFoundError
from aries_cloudagent.protocols.problem_report.v1_0.message import ProblemReport
from aries_cloudagent.utils.tracing import AdminAPIMessageTracingSchema

from .util import generate_model_schema, admin_only

PROTOCOL_URI = "did:sov:BzCbsNYhMrjHiqZDTUASHg;spec/admin-issuer/0.1"

ISSUER_SEND_CREDENTIAL = "{}/send-credential".format(PROTOCOL_URI)
ISSUER_REQUEST_PRESENTATION = "{}/request-presentation".format(PROTOCOL_URI)
ISSUER_ISSUER_CRED_EXCHANGE = "{}/credential-exchange".format(PROTOCOL_URI)
ISSUER_ISSUER_PRES_EXCHANGE = "{}/presentation-exchange".format(PROTOCOL_URI)
ISSUER_CREDENTIALS_GET_LIST = "{}/credentials-get-list".format(PROTOCOL_URI)
ISSUER_CREDENTIALS_LIST = "{}/credentials-list".format(PROTOCOL_URI)
ISSUER_PRESENTATIONS_GET_LIST = "{}/presentations-get-list".format(PROTOCOL_URI)
ISSUER_PRESENTATIONS_LIST = "{}/presentations-list".format(PROTOCOL_URI)

MESSAGE_TYPES = {
    ISSUER_SEND_CREDENTIAL: "acapy_plugin_toolbox.issuer.SendCred",
    ISSUER_REQUEST_PRESENTATION: "acapy_plugin_toolbox.issuer.RequestPres",
    ISSUER_CREDENTIALS_GET_LIST: "acapy_plugin_toolbox.issuer.CredGetList",
    ISSUER_CREDENTIALS_LIST: "acapy_plugin_toolbox.issuer.CredList",
    ISSUER_PRESENTATIONS_GET_LIST: "acapy_plugin_toolbox.issuer.PresGetList",
    ISSUER_PRESENTATIONS_LIST: "acapy_plugin_toolbox.issuer.PresList",
}


SendCred, SendCredSchema = generate_model_schema(
    name="SendCred",
    handler="acapy_plugin_toolbox.issuer.SendCredHandler",
    msg_type=ISSUER_SEND_CREDENTIAL,
    schema=V10CredentialProposalRequestMandSchema,
)
IssuerCredExchange, IssuerCredExchangeSchema = generate_model_schema(
    name="IssuerCredExchange",
    handler="acapy_plugin_toolbox.util.PassHandler",
    msg_type=ISSUER_ISSUER_CRED_EXCHANGE,
    schema=V10CredentialExchangeSchema,
)


class SendCredHandler(BaseHandler):
    """Handler for received send request."""

    @admin_only
    async def handle(self, context: RequestContext, responder: BaseResponder):
        """Handle received send request."""
        comment = context.message.comment
        connection_id = str(context.message.connection_id)
        preview_spec = context.message.credential_proposal

        try:
            connection_record = await ConnectionRecord.retrieve_by_id(
                context, connection_id
            )
        except StorageNotFoundError:
            report = ProblemReport(
                explain_ltxt="Connection not found.", who_retries="none"
            )
            report.assign_thread_from(context.message)
            await responder.send_reply(report)
            return

        if not connection_record.is_ready:
            report = ProblemReport(
                explain_ltxt="Connection invalid.", who_retries="none"
            )
            report.assign_thread_from(context.message)
            await responder.send_reply(report)
            return

        credential_proposal = CredentialProposal(
            comment=comment,
            credential_proposal=preview_spec,
            **{
                t: getattr(context.message, t)
                for t in CRED_DEF_TAGS
                if hasattr(context.message, t)
            },
        )

        credential_manager = CredentialManager(context)
        (
            cred_exchange_record,
            cred_offer_message,
        ) = await credential_manager.prepare_send(
            connection_id, credential_proposal=credential_proposal
        )

        await responder.send(
            cred_offer_message, connection_id=cred_exchange_record.connection_id
        )
        cred_exchange = IssuerCredExchange(**cred_exchange_record.serialize())
        cred_exchange.assign_thread_from(context.message)
        await responder.send_reply(cred_exchange)


## class from acapy 0.4
class V10PresentationRequestRequestSchema(AdminAPIMessageTracingSchema):
    """Request schema for sending a proof request."""

    connection_id = fields.UUID(
        description="Connection identifier", required=True
    )
    proof_request = fields.Nested(IndyProofRequestSchema(), required=True)
    comment = fields.Str(required=False)

RequestPres, RequestPresSchema = generate_model_schema(
    name="RequestPres",
    handler="acapy_plugin_toolbox.issuer.RequestPresHandler",
    msg_type=ISSUER_REQUEST_PRESENTATION,
    schema=V10PresentationRequestRequestSchema,
)
IssuerPresExchange, IssuerPresExchangeSchema = generate_model_schema(
    name="IssuerPresExchange",
    handler="acapy_plugin_toolbox.util.PassHandler",
    msg_type=ISSUER_ISSUER_PRES_EXCHANGE,
    schema=V10PresentationExchangeSchema,
)


class RequestPresHandler(BaseHandler):
    """Handler for received presentation request request."""

    @admin_only
    async def handle(self, context: RequestContext, responder: BaseResponder):
        """Handle received presentation request request."""

        connection_id = str(context.message.connection_id)
        try:
            connection_record = await ConnectionRecord.retrieve_by_id(
                context, connection_id
            )
        except StorageNotFoundError:
            report = ProblemReport(
                explain_ltxt="Connection not found.", who_retries="none"
            )
            report.assign_thread_from(context.message)
            await responder.send_reply(report)
            return

        if not connection_record.is_ready:
            report = ProblemReport(
                explain_ltxt="Connection invalid.", who_retries="none"
            )
            report.assign_thread_from(context.message)
            await responder.send_reply(report)
            return

        comment = context.message.comment

        indy_proof_request = context.message.proof_request
        if not indy_proof_request.get("nonce"):
            indy_proof_request["nonce"] = str(uuid4().int)

        presentation_request_message = PresentationRequest(
            comment=comment,
            request_presentations_attach=[
                AttachDecorator.from_indy_dict(indy_proof_request)
            ],
        )

        presentation_manager = PresentationManager(context)

        presentation_exchange_record = await presentation_manager.create_exchange_for_request(
            connection_id=connection_id,
            presentation_request_message=presentation_request_message,
        )

        await responder.send(presentation_request_message, connection_id=connection_id)

        pres_exchange = IssuerPresExchange(**presentation_exchange_record.serialize())
        pres_exchange.assign_thread_from(context.message)
        await responder.send_reply(pres_exchange)


CredGetList, CredGetListSchema = generate_model_schema(
    name="CredGetList",
    handler="acapy_plugin_toolbox.issuer.CredGetListHandler",
    msg_type=ISSUER_CREDENTIALS_GET_LIST,
    schema={
        "connection_id": fields.Str(required=False),
        "cred_def_id": fields.Str(required=False),
        "schema_id": fields.Str(required=False),
    },
)

CredList, CredListSchema = generate_model_schema(
    name="CredList",
    handler="acapy_plugin_toolbox.util.PassHandler",
    msg_type=ISSUER_CREDENTIALS_LIST,
    schema=V10CredentialExchangeListResultSchema,
)


class CredGetListHandler(BaseHandler):
    """Handler for received get cred list request."""

    @admin_only
    async def handle(self, context: RequestContext, responder: BaseResponder):
        """Handle received get cred list request."""

        post_filter = dict(
            filter(
                lambda item: item[1] is not None,
                {
                    # 'state': V10CredentialExchange.STATE_ISSUED,
                    "role": V10CredentialExchange.ROLE_ISSUER,
                    "connection_id": context.message.connection_id,
                    "credential_definition_id": context.message.cred_def_id,
                    "schema_id": context.message.schema_id,
                }.items(),
            )
        )
        records = await V10CredentialExchange.query(context, {}, post_filter)
        cred_list = CredList(results=records)
        await responder.send_reply(cred_list)


PresGetList, PresGetListSchema = generate_model_schema(
    name="PresGetList",
    handler="acapy_plugin_toolbox.issuer.PresGetListHandler",
    msg_type=ISSUER_PRESENTATIONS_GET_LIST,
    schema={
        "connection_id": fields.Str(required=False),
        "verified": fields.Str(required=False),
    },
)

PresList, PresListSchema = generate_model_schema(
    name="PresList",
    handler="acapy_plugin_toolbox.util.PassHandler",
    msg_type=ISSUER_PRESENTATIONS_LIST,
    schema=V10PresentationExchangeListSchema
    # schema={
    #     'results': fields.List(fields.Dict())
    # }
)


class PresGetListHandler(BaseHandler):
    """Handler for received get cred list request."""

    @admin_only
    async def handle(self, context: RequestContext, responder: BaseResponder):
        """Handle received get cred list request."""

        post_filter = dict(
            filter(
                lambda item: item[1] is not None,
                {
                    # 'state': V10PresentialExchange.STATE_CREDENTIAL_RECEIVED,
                    "role": V10PresentationExchange.ROLE_VERIFIER,
                    "connection_id": context.message.connection_id,
                    "verified": context.message.verified,
                }.items(),
            )
        )
        records = await V10PresentationExchange.query(context, {}, post_filter)
        cred_list = PresList(results=records)
        await responder.send_reply(cred_list)
