from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.api.dependencies import (
    OrganizationContext,
    get_database_session,
    require_roles,
)
from app.documents.models import DocumentErrorCode
from app.documents.ports import DocumentStorage, DocumentTaskDispatcher
from app.documents.repository import DocumentWithVersion
from app.documents.schemas import (
    DocumentStatusResponse,
    DocumentVersionStatusResponse,
)
from app.documents.service import (
    DocumentDispatchError,
    DocumentNotFoundError,
    DocumentPersistenceError,
    DocumentService,
    DocumentStorageUnavailableError,
)
from app.documents.validation import (
    DocumentTooLargeError,
    DocumentValidationError,
)
from app.identity.models import MembershipRole


router = APIRouter(prefix="/api/v1/documents", tags=["documents"])
require_admin = require_roles(MembershipRole.ADMIN)

SAFE_ERROR_MESSAGES = {
    DocumentErrorCode.DISPATCH_FAILED.value: (
        "Document processing could not be queued"
    ),
}


def get_document_storage(request: Request) -> DocumentStorage:
    return request.app.state.document_storage


def get_document_task_dispatcher(request: Request) -> DocumentTaskDispatcher:
    return request.app.state.document_task_dispatcher


def get_document_max_upload_bytes(request: Request) -> int:
    return int(request.app.state.document_max_upload_bytes)


async def require_single_file_field(request: Request) -> None:
    form = await request.form()
    file_values = form.getlist("file")
    if (
        set(form.keys()) != {"file"}
        or len(file_values) != 1
        or not isinstance(file_values[0], StarletteUploadFile)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "invalid_document_form",
                "message": "Multipart upload must contain exactly one file field",
                "details": None,
            },
        )


def document_status_response(
    result: DocumentWithVersion,
) -> DocumentStatusResponse:
    document = result.document
    version = result.version
    return DocumentStatusResponse(
        id=document.id,
        source_type=document.source_type,
        display_filename=document.display_filename,
        created_at=document.created_at,
        updated_at=document.updated_at,
        version=DocumentVersionStatusResponse(
            id=version.id,
            version_number=version.version_number,
            media_type=version.media_type,
            size_bytes=version.size_bytes,
            status=version.status,
            error_code=version.error_code,
            error_message=(
                SAFE_ERROR_MESSAGES.get(
                    version.error_code,
                    "Document processing failed",
                )
                if version.error_code is not None
                else None
            ),
            created_at=version.created_at,
            updated_at=version.updated_at,
        ),
    )


@router.post(
    "",
    response_model=DocumentStatusResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_document(
    response: Response,
    context: Annotated[OrganizationContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
    _strict_form: Annotated[None, Depends(require_single_file_field)],
    file: Annotated[UploadFile, File()],
    storage: Annotated[DocumentStorage, Depends(get_document_storage)],
    dispatcher: Annotated[
        DocumentTaskDispatcher,
        Depends(get_document_task_dispatcher),
    ],
    max_upload_bytes: Annotated[int, Depends(get_document_max_upload_bytes)],
) -> DocumentStatusResponse:
    service = DocumentService(
        session,
        context.organization.id,
        storage=storage,
        dispatcher=dispatcher,
        max_upload_bytes=max_upload_bytes,
    )
    try:
        created = service.create_upload(
            filename=file.filename,
            source=file.file,
            actor_user_id=context.membership.user_id,
        )
    except DocumentTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail={
                "code": exc.code,
                "message": exc.user_message,
                "details": None,
            },
        ) from None
    except DocumentValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": exc.code,
                "message": exc.user_message,
                "details": None,
            },
        ) from None
    except DocumentStorageUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "document_storage_unavailable",
                "message": "Document storage is temporarily unavailable",
                "details": None,
            },
        ) from None
    except DocumentPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "document_persistence_failed",
                "message": "Document could not be created",
                "details": None,
            },
        ) from None
    except DocumentDispatchError as exc:
        location = f"/api/v1/documents/{exc.document_id}"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Location": location},
            detail={
                "code": DocumentErrorCode.DISPATCH_FAILED.value,
                "message": "Document processing could not be queued",
                "details": {
                    "document_id": str(exc.document_id),
                    "status_url": location,
                },
            },
        ) from None

    result = DocumentWithVersion(
        document=created.document,
        version=created.version,
    )
    response.headers["Location"] = f"/api/v1/documents/{created.document.id}"
    return document_status_response(result)


@router.get("/{document_id}", response_model=DocumentStatusResponse)
def get_document_status(
    document_id: UUID,
    context: Annotated[OrganizationContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_database_session)],
    storage: Annotated[DocumentStorage, Depends(get_document_storage)],
    dispatcher: Annotated[
        DocumentTaskDispatcher,
        Depends(get_document_task_dispatcher),
    ],
    max_upload_bytes: Annotated[int, Depends(get_document_max_upload_bytes)],
) -> DocumentStatusResponse:
    service = DocumentService(
        session,
        context.organization.id,
        storage=storage,
        dispatcher=dispatcher,
        max_upload_bytes=max_upload_bytes,
    )
    try:
        result = service.get_status(document_id)
    except DocumentNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        ) from None
    return document_status_response(result)
