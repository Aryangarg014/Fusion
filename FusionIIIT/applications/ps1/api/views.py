"""
api/views.py — PS1 Purchase & Store Module
==========================================
Refactored: views are query-free — all DB reads go via selectors,
all business logic goes via services.
Bug fixes applied:
  1. stockTransfer: indent.item_type → indent.items.first().item_type
  2. stockEntry: vendor=vendor → vendor_id=vendor
  3. Added missing cancel_indent_view (referenced in urls.py)
"""
import json
import logging
import ast
from datetime import datetime

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Q, Count
from django.http import HttpResponseForbidden, JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import status  # type: ignore
from rest_framework.decorators import api_view, permission_classes  # type: ignore
from rest_framework.permissions import IsAuthenticated  # type: ignore
from rest_framework.response import Response  # type: ignore

from applications.ps1 import selectors, services
from applications.ps1.models import (
    IndentFile, IndentItem, StockEntry, StockItem, StockTransfer,
    GRN, PurchaseOrder, Invoice, Vendor,
)
from applications.globals.models import (
    HoldsDesignation, Designation, ExtraInfo, DepartmentInfo, Faculty,
)
from applications.filetracking.models import File, Tracking
from applications.filetracking.sdk.methods import (
    create_draft, create_file, forward_file, archive_file,
    view_inbox, view_outbox, view_archived,
)
from notification.views import office_module_notif, purchase_notif, iwd_notif

from .serializers import (
    ExtraInfoSerializer, FileSerializer, HoldsDesignationSerializer,
    IndentFileSerializer, IndentItemSerializer, StockEntrySerializer,
    StockItemSerializer, StockTransferSerializer, TrackingSerializer,
    VendorSerializer, GRNSerializer,
)

logger = logging.getLogger(__name__)


def _parse_items_payload(request_data):
    items = request_data.get('items')
    if not items:
        single_item = {
            'item_name': request_data.get('item_name', ''),
            'quantity': request_data.get('quantity', 0),
            'present_stock': request_data.get('present_stock', 0),
            'estimated_cost': request_data.get('estimated_cost'),
            'purpose': request_data.get('purpose', ''),
            'specification': request_data.get('specification', ''),
            'item_type': request_data.get('item_type', ''),
            'item_subtype': request_data.get('item_subtype', 'computers'),
            'nature': request_data.get('nature', False),
            'indigenous': request_data.get('indigenous', False),
            'replaced': request_data.get('replaced', False),
            'budgetary_head': request_data.get('budgetary_head', ''),
            'expected_delivery': request_data.get('expected_delivery'),
            'sources_of_supply': request_data.get('sources_of_supply', ''),
        }
        items = [single_item]

    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = []

    normalized_items = []
    for item in items or []:
        normalized_items.append({
            'item_name': item.get('item_name', '').strip(),
            'quantity': int(item.get('quantity') or 0),
            'present_stock': int(item.get('present_stock') or 0),
            'estimated_cost': int(float(item.get('estimated_cost') or 0)),
            'purpose': item.get('purpose', '').strip(),
            'specification': item.get('specification', '').strip(),
            'item_type': item.get('item_type', '').strip(),
            'item_subtype': item.get('item_subtype', 'computers').strip(),
            'nature': bool(item.get('nature')),
            'indigenous': bool(item.get('indigenous')),
            'replaced': bool(item.get('replaced')),
            'budgetary_head': item.get('budgetary_head', '').strip(),
            'expected_delivery': item.get('expected_delivery'),
            'sources_of_supply': item.get('sources_of_supply', '').strip(),
        })

    return normalized_items


def _validate_items_payload(items_data):
    if not items_data:
        raise ValidationError('At least one item is required (BR-PS-001).')

    for item in items_data:
        missing = [field for field in ['item_name', 'purpose', 'specification', 'item_type', 'budgetary_head', 'sources_of_supply'] if not item.get(field)]
        if missing:
            raise ValidationError(f"Missing mandatory item fields: {', '.join(missing)} (BR-PS-001).")
        if int(item.get('quantity') or 0) <= 0:
            raise ValidationError('Quantity must be greater than zero (BR-PS-002).')
        if int(item.get('estimated_cost') or 0) <= 0:
            raise ValidationError('Estimated cost must be greater than zero (BR-PS-002).')


def _maybe_get_receiver(request_data):
    receiver_username = request_data.get('forwardTo') or request_data.get('receiver')
    receiver_designation_name = request_data.get('receiverDesignation') or request_data.get('recieve') or request_data.get('receive_designation')
    if not receiver_username or not receiver_designation_name:
        return None, None
    receiver_user = User.objects.filter(username=receiver_username).first()
    if not receiver_user:
        return None, None
    receiver_hd = HoldsDesignation.objects.select_related('designation').filter(
        user=receiver_user,
        designation__name=receiver_designation_name,
    ).first()
    return receiver_user, receiver_hd


def _resolve_sender_receiver_context(request, file_id):
    """Resolve sender/receiver HoldsDesignation from mixed payload formats.

    Supports both:
    1) sender/receive as HoldsDesignation IDs
    2) forwardTo + receiverDesignation (+ optional role)
    """
    sender_hd = None
    receiver_hd = None

    sender_raw = request.data.get('sender')
    receiver_raw = request.data.get('receive')
    sender_role = request.data.get('role')
    forward_to = request.data.get('forwardTo') or request.data.get('receiver')
    receiver_designation_name = (
        request.data.get('receiverDesignation')
        or request.data.get('recieve')
        or request.data.get('receive_designation')
    )

    # Sender resolution
    if sender_raw:
        try:
            sender_hd = HoldsDesignation.objects.select_related('designation').get(id=int(sender_raw))
        except Exception:
            sender_hd = None

    if sender_hd is None and sender_role:
        sender_hd = HoldsDesignation.objects.select_related('designation').filter(
            user=request.user,
            designation__name=sender_role
        ).first()

    if sender_hd is None:
        latest_track = Tracking.objects.select_related('current_design').filter(
            file_id_id=file_id
        ).order_by('-forward_date').first()
        if latest_track and latest_track.current_design and latest_track.current_design.user == request.user:
            sender_hd = latest_track.current_design

    if sender_hd is None:
        sender_hd = HoldsDesignation.objects.select_related('designation').filter(user=request.user).first()

    # Receiver resolution
    if receiver_raw:
        try:
            receiver_hd = HoldsDesignation.objects.select_related('user', 'designation').get(id=int(receiver_raw))
        except Exception:
            receiver_hd = None

    if receiver_hd is None and forward_to and receiver_designation_name:
        receiver_user = User.objects.filter(username=forward_to).first()
        if receiver_user:
            receiver_hd = HoldsDesignation.objects.select_related('user', 'designation').filter(
                user=receiver_user,
                designation__name=receiver_designation_name,
            ).first()

    if receiver_hd is None and receiver_raw and not str(receiver_raw).isdigit():
        receiver_hd = HoldsDesignation.objects.select_related('user', 'designation').filter(
            designation__name=receiver_raw
        ).first()

    if sender_hd is None:
        raise ValidationError('Could not resolve sender designation for current user.')
    if receiver_hd is None:
        raise ValidationError(
            'Could not resolve receiver designation. Provide valid receiver username and designation.'
        )

    return sender_hd, receiver_hd

# ---------------------------------------------------------------------------
# Role constants (sourced from services.py to avoid duplication)
# ---------------------------------------------------------------------------
from applications.ps1.services import DEPT_ADMIN_ROLES, DEPT_ADMIN_TO_DEPT

dept_admin_design = DEPT_ADMIN_ROLES  # backward-compat alias
dept_admin_to_dept = DEPT_ADMIN_TO_DEPT


# ===========================================================================
# DESIGNATION / USER HELPERS
# ===========================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def getDesignations(request):
    try:
        designations = HoldsDesignation.objects.filter(user=request.user)
        if not designations.exists():
            return Response({"error": "No designations found for the user."},
                            status=status.HTTP_404_NOT_FOUND)
        serialized = HoldsDesignationSerializer(designations, many=True)
        return Response(serialized.data, status=status.HTTP_200_OK)
    except Exception as exc:
        return Response({"error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_suggestions(request):
    """T-09: Removed hardcoded usernames — returns real staff users."""
    try:
        suggestions = selectors.get_user_suggestions()
        users = [
            {
                'username': u['user__username'],
                'name': f"{u['user__first_name']} {u['user__last_name']}".strip(),
                'department': u['department__name'],
            }
            for u in suggestions
        ]
        return Response({'users': users}, status=status.HTTP_200_OK)
    except Exception as exc:
        return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ===========================================================================
# INDENT FILING
# ===========================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def createProposal(request):
    """UC-001: File a new indent proposal."""
    try:
        user = request.user
        holds_designation = HoldsDesignation.objects.filter(user=user).first()
        if not holds_designation:
            return Response({"error": "No designation found for user."},
                            status=status.HTTP_400_BAD_REQUEST)
        designation_obj = holds_designation.designation

        title = request.data.get('title', '').strip()
        description = request.data.get('description', '')
        upload_file = request.FILES.get('file') or request.FILES.get('myfile')
        items_data = _parse_items_payload(request.data)
        _validate_items_payload(items_data)

        receiver_user, receiver_hd = _maybe_get_receiver(request.data)
        if receiver_user is None or receiver_hd is None:
            return Response(
                {'error': 'Valid forwardTo and receiverDesignation are required for submission.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        indent = services.create_proposal(
            uploader_user=user,
            title=title,
            description=description,
            upload_file=upload_file,
            items_data=items_data,
            designation_obj=designation_obj,
            receiver_user=receiver_user,
            receiver_designation=receiver_hd.designation,
            remarks=request.data.get('remarks', ''),
        )
        return Response({
            'indent_file': IndentFileSerializer(indent).data,
            'message': 'Indent Filed Successfully!',
        }, status=status.HTTP_201_CREATED)

    except ValidationError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        logger.error("createProposal error: %s", exc)
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def createDraft(request):
    """UC-002: Save an indent as draft."""
    try:
        user = request.user
        holds_designation = HoldsDesignation.objects.filter(user=user).first()
        if not holds_designation:
            return Response({"error": "No designation found."}, status=status.HTTP_400_BAD_REQUEST)
        designation_obj = holds_designation.designation

        title = request.data.get('title', '').strip()
        description = request.data.get('description', '')
        upload_file = request.FILES.get('file') or request.FILES.get('myfile')
        items_data = _parse_items_payload(request.data)

        # Drafts may contain incomplete data, but if an item is present,
        # keep the positive-value guards so bad values don't get persisted.
        for item in items_data:
            if item.get('quantity') is not None and int(item.get('quantity') or 0) < 0:
                raise ValidationError('Quantity cannot be negative (BR-PS-002).')
            if item.get('estimated_cost') is not None and int(item.get('estimated_cost') or 0) < 0:
                raise ValidationError('Estimated cost cannot be negative (BR-PS-002).')

        indent = services.create_draft_indent(
            uploader_user=user,
            title=title,
            description=description,
            upload_file=upload_file,
            items_data=items_data,
            designation_obj=designation_obj,
        )
        return Response({
            'indent_file': IndentFileSerializer(indent).data,
            'message': 'Draft saved successfully!',
        }, status=status.HTTP_201_CREATED)

    except ValidationError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def delete_indent(request):
    """
    DEPRECATED in favour of cancel_indent_view (T-02, BR-PS-011).
    Kept for backward compatibility with older frontend.
    """
    try:
        file_id = request.data.get('file_id')
        services.delete_indent(file_id)
        return Response({"message": "Indent deleted successfully."}, status=status.HTTP_200_OK)
    except IndentFile.DoesNotExist:
        return Response({"error": "Indent not found."}, status=status.HTTP_404_NOT_FOUND)
    except Exception as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def getOneFiledIndent(request):
    """Return a single indent's full detail."""
    try:
        file_id = request.data.get('file_id')
        indent = IndentFile.objects.get(file_info_id=file_id)
        file_info = selectors.get_file_by_id(file_id)
        items = IndentItem.objects.filter(indent_file_id=file_id)

        department = request.user.extrainfo.department.name
        return Response({
            'indent': IndentFileSerializer(indent).data,
            'file': FileSerializer(file_info).data,
            'department': department,
            'items': IndentItemSerializer(items, many=True).data,
        }, status=status.HTTP_200_OK)

    except IndentFile.DoesNotExist:
        return Response({"error": "Indent not found."}, status=status.HTTP_404_NOT_FOUND)
    except Exception as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def forwardIndent(request, id):
    """Forward an indent to the next approver using HoldsDesignation IDs."""
    try:
        remarks = request.data.get('remarks', '')
        upload_file = (
            request.FILES.get('myfile')
            or request.FILES.get('file')
            or request.FILES.get('file_attachment')
        )

        sender_hd, receiver_hd = _resolve_sender_receiver_context(request, id)

        indent = services.forward_indent(
            file_id=id,
            sender_hd_id=sender_hd.id,
            receiver_hd_id=receiver_hd.id,
            remarks=remarks,
            upload_file=upload_file,
        )

        office_module_notif(request.user, receiver_hd.user)

        return Response({
            'indent_file': IndentFileSerializer(indent).data,
            'message': 'Indent Forwarded successfully',
        }, status=status.HTTP_200_OK)

    except ValidationError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


# ===========================================================================
# INDENT VIEWS (Inbox / Outbox / Draft / Archive)
# ===========================================================================

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def indentView(request, username):
    user = User.objects.get(username=username)
    user_id = user.id

    hold_designation = HoldsDesignation.objects.filter(user_id=user_id)
    hd_id = hold_designation[0].id
    currentDesignation = request.GET.get('role')
    if currentDesignation == "student":
        return Response({'error': 'Students are not allowed to access this view'}, status=403)

    designation = HoldsDesignation.objects.filter(
        user=request.user, designation__name=currentDesignation
    ).first()

    tracking_objects = Tracking.objects.all()
    tracking_obj_ids = [obj.file_id for obj in tracking_objects]
    draft_indent = IndentFile.objects.filter(file_info__in=tracking_obj_ids)
    draft = [indent.file_info.id for indent in draft_indent]
    draft_files = File.objects.filter(id__in=draft).order_by('-upload_date')
    indents = [file.indentfile for file in draft_files]

    serializer = IndentFileSerializer(indents, many=True)
    serializer_draft = FileSerializer(draft_files, many=True)

    combined_data = [
        {'indent': ind, 'draft_file': df}
        for ind, df in zip(serializer.data, serializer_draft.data)
    ]

    notifs = list(request.user.notifications.all().values())
    return Response({'Data': combined_data, 'notifications': notifs})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def indentView2(request, username):
    user = User.objects.get(username=username)
    user_id = user.id
    current_designation_name = request.GET.get('role')

    if current_designation_name == "student":
        return Response({'error': 'Students are not allowed to access this view'}, status=403)

    designation = HoldsDesignation.objects.filter(
        user=request.user, designation__name=current_designation_name
    ).first()
    if not designation:
        return Response({'error': 'Designation not found'}, status=404)

    abcd = HoldsDesignation.objects.filter(
        user_id=user_id, designation__name=current_designation_name
    ).first()
    if not abcd:
        return Response({'error': 'User does not hold the specified designation.'}, status=404)

    designations = abcd.designation.name
    data = view_inbox(request.user.username, designations, "ps1")

    for item in data:
        file_id = item['id']
        tracking_entry = Tracking.objects.filter(
            file_id=file_id,
            receiver_id=user,
            receive_design__name=current_designation_name
        ).first()
        if tracking_entry:
            item['receiver_id_id'] = tracking_entry.receiver_id.id if tracking_entry.receiver_id else None
            item['receiver_design_id'] = tracking_entry.receive_design.id if tracking_entry.receive_design else None
            item['receiver_designation_name'] = tracking_entry.receive_design.name if tracking_entry.receive_design else None

    data = sorted(data, key=lambda x: datetime.fromisoformat(x['upload_date']), reverse=True)
    for item in data:
        item['upload_date'] = datetime.fromisoformat(item['upload_date'])

    notifs = list(request.user.notifications.all().values())
    return Response({
        'receive_design': HoldsDesignationSerializer(abcd).data,
        'in_file': data,
        'department': request.user.extrainfo.department.name,
        'notifications': notifs,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def draftView(request, username):
    user = User.objects.get(username=username)
    user_id = user.id
    hold_designation = HoldsDesignation.objects.filter(user_id=user_id)
    hd_id = hold_designation[0].id

    designation_str = str(Designation.objects.get(
        id=HoldsDesignation.objects.select_related('designation').get(id=hd_id).designation_id
    ))
    if designation_str == "student":
        return Response({"message": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    indents = IndentFile.objects.filter(
        file_info__in=request.user.extrainfo.uploaded_files.all()
    ).select_related('file_info')

    department = request.user.extrainfo.department.name
    indent_ids = [indent.file_info for indent in indents]
    filed_indents = Tracking.objects.filter(file_id__in=indent_ids)
    filed_indent_ids = [indent.file_id for indent in filed_indents]
    draft = list(set(indent_ids) - set(filed_indent_ids))
    draft_indent = IndentFile.objects.filter(file_info__in=draft).values("file_info")
    draft_files = File.objects.filter(id__in=draft_indent).order_by('-upload_date')

    return Response({
        "department": department,
        "files": FileSerializer(draft_files, many=True).data,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def inwardIndents(request, id):
    designation = str(Designation.objects.get(
        id=HoldsDesignation.objects.select_related('designation').get(id=id).designation_id
    ))
    abcd = HoldsDesignation.objects.get(pk=id)
    data = view_inbox(request.user.username, designation, "ps1")
    data = sorted(data, key=lambda x: datetime.fromisoformat(x['upload_date']), reverse=True)
    for item in data:
        item['upload_date'] = datetime.fromisoformat(item['upload_date'])

    return Response({'receive_design': str(abcd), 'in_file': data})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def indentFile(request, id):
    try:
        indent_file = IndentFile.objects.select_related('file_info').get(file_info=id)
    except IndentFile.DoesNotExist:
        return Response({"message": "Indent file does not exist"}, status=status.HTTP_404_NOT_FOUND)

    track = selectors.get_tracking_for_file(indent_file.file_info)
    extrainfo = ExtraInfo.objects.select_related('user', 'department').all()
    holdsdesignations = HoldsDesignation.objects.select_related('user', 'working', 'designation').all()
    designations = HoldsDesignation.objects.select_related('user', 'working', 'designation').filter(
        user=request.user
    )

    return Response({
        'indent_file': IndentFileSerializer(indent_file).data,
        'track': TrackingSerializer(track, many=True).data,
        'extrainfo': ExtraInfoSerializer(extrainfo, many=True).data,
        'holdsdesignations': HoldsDesignationSerializer(holdsdesignations, many=True).data,
        'designations': HoldsDesignationSerializer(designations, many=True).data,
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def ForwardIndentFile(request, id):
    try:
        indent = IndentFile.objects.select_related('file_info').get(file_info=id)
        file = indent.file_info_id
        track = selectors.get_tracking_for_file(indent.file_info)
    except IndentFile.DoesNotExist:
        return Response({"message": "Indent file does not exist"}, status=status.HTTP_404_NOT_FOUND)

    remarks = request.data.get('remarks', '')
    upload_file = (
        request.FILES.get('myfile')
        or request.FILES.get('file')
        or request.FILES.get('file_attachment')
    )

    try:
        sender_hd, receiver_hd = _resolve_sender_receiver_context(request, file.id)
        indent = services.forward_indent(
            file_id=file.id,
            sender_hd_id=sender_hd.id,
            receiver_hd_id=receiver_hd.id,
            remarks=remarks,
            upload_file=upload_file,
        )
        office_module_notif(request.user, receiver_hd.user)
    except ValidationError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        'indent_file': IndentFileSerializer(indent).data,
        'track': TrackingSerializer(track, many=True).data,
        'message': 'Indent File Forwarded successfully',
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def archieve_file(request, id):
    file_id = request.GET.get('file_id')
    res = archive_file(file_id)
    if res:
        return Response({"message": "File has been archived successfully"})
    return Response({"message": "Unsuccessful in archiving file"})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def archieveview(request, username):
    user = User.objects.get(username=username)
    user_id = user.id
    currentDesignation = request.GET.get('role')
    if currentDesignation == "student":
        return Response({'error': 'Students are not allowed to access this view'}, status=403)

    designation = HoldsDesignation.objects.filter(
        user=request.user, designation__name=currentDesignation
    ).first()
    if not designation:
        return Response({'error': 'Designation not found or mismatch'}, status=404)

    abcd = HoldsDesignation.objects.filter(
        user_id=user_id, designation__name=currentDesignation
    ).first()
    if not abcd:
        return Response({'error': 'User does not hold the specified designation.'}, status=404)

    designations = abcd.designation.name
    archived_files = view_archived(
        username=request.user,
        designation=designations,
        src_module="ps1"
    )
    for files in archived_files:
        files['upload_date'] = datetime.fromisoformat(files['upload_date'])
        files['upload_date'] = files['upload_date'].strftime("%B %d, %Y, %I:%M %p")

    notifs = list(request.user.notifications.all().values())
    return Response({
        'archieves': archived_files,
        'designations': designations,
        'notifications': notifs,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def outboxview2(request, username):
    user = User.objects.get(username=username)
    user_id = user.id
    currentDesignation = request.GET.get('role')

    if currentDesignation == "student":
        return Response({'error': 'Students are not allowed to access this view'}, status=403)

    abcd = HoldsDesignation.objects.filter(
        user_id=user_id, designation__name=currentDesignation
    ).first()
    if not abcd:
        return Response({'error': 'Designation not found.'}, status=404)

    designations = abcd.designation.name
    outbox_data = view_outbox(request.user.username, designations, "ps1")
    outbox_data = sorted(outbox_data, key=lambda x: datetime.fromisoformat(x['upload_date']), reverse=True)
    for item in outbox_data:
        item['upload_date'] = datetime.fromisoformat(item['upload_date'])

    notifs = list(request.user.notifications.all().values())
    return Response({
        'in_file': outbox_data,
        'designations': designations,
        'notifications': notifs,
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_indents_view(request, username):
    """T-01: Paginated list of all indents for the requesting user."""
    try:
        user = User.objects.get(username=username)
        page = int(request.GET.get('page', 1))
        indents = selectors.get_my_indents(user, page=page)
        return Response({
            'results': IndentFileSerializer(indents, many=True).data,
            'page': page,
        })
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as exc:
        return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def approve_indent(request):
    """UC-007: Approve an indent."""
    try:
        file_id = request.data.get('indent_id') or request.data.get('file_id')
        approval_data = request.data.get('approval_data', '')
        indent = services.approve_indent(file_id, request.user, approval_data)
        return Response({'message': 'Indent approved', 'approved_by': indent.approved_by})
    except Exception as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


# ===========================================================================
# STOCK ENTRY / CURRENT STOCK
# ===========================================================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def entry(request, id):
    designation = str(Designation.objects.get(
        id=HoldsDesignation.objects.select_related('designation').get(id=id).designation_id
    ))

    if request.method == 'GET':
        if designation not in dept_admin_design + ["ps_admin"]:
            return Response({"message": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)
        department = request.user.extrainfo.department
        if designation in dept_admin_design:
            indent_files = selectors.get_indents_for_department(department)
        else:
            indent_files = selectors.get_all_filed_indents()
        return Response(IndentFileSerializer(indent_files, many=True).data)

    elif request.method == 'POST':
        if str(designation) not in dept_admin_design + ["ps_admin"]:
            return Response({"message": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)
        item_id = request.data.get('id')
        if not item_id:
            return Response({"message": "ID parameter is required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            request_file = File.objects.select_related('uploader').get(id=item_id)
            requester = request_file.uploader.user
            corresponding_indent_file = IndentFile.objects.get(file_info=request_file)
            return Response({
                'request_file': FileSerializer(request_file).data,
                'requester': requester.username,
                'corresponding_indent_file': IndentFileSerializer(corresponding_indent_file).data,
            })
        except File.DoesNotExist:
            return Response({"message": "File not found"}, status=status.HTTP_404_NOT_FOUND)
        except IndentFile.DoesNotExist:
            return Response({"message": "Indent file not found"}, status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def stockEntryView(request, id):
    designation = str(Designation.objects.get(
        id=HoldsDesignation.objects.select_related('designation').get(id=id).designation_id
    ))
    if str(designation) not in dept_admin_design + ["ps_admin"]:
        return Response({"message": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

    department = request.user.extrainfo.department
    if designation in dept_admin_design:
        stocks = selectors.get_stock_entries_for_department(department)
    elif designation == "ps_admin":
        stocks = selectors.get_all_stock_entries()
    else:
        return Response({"message": "Invalid designation"}, status=status.HTTP_400_BAD_REQUEST)

    return Response(StockEntrySerializer(stocks, many=True).data)


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def currentStockView(request, id):
    designation = str(Designation.objects.get(
        id=HoldsDesignation.objects.select_related('designation').get(id=id).designation_id
    ))

    if request.method == 'GET':
        if str(designation) not in dept_admin_design + ["ps_admin"]:
            return Response({"message": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)
        department = request.user.extrainfo.department
        if designation in dept_admin_design:
            stocks = selectors.get_stock_items_for_department(department)
        elif designation == "ps_admin":
            stocks = selectors.get_all_stock_items()
        else:
            return Response({"message": "Invalid designation"}, status=status.HTTP_403_FORBIDDEN)

        grouped_items = selectors.get_grouped_stock_summary(stocks)
        grouped_items_list = [
            {
                'item_type': item['StockEntryId__item_id__item_type'],
                'department': DepartmentInfo.objects.get(id=item['department']).name,
                'total_quantity': item['total_quantity'],
            }
            for item in grouped_items
        ]
        return Response(grouped_items_list)

    elif request.method == 'POST':
        dept_id = request.data.get('department')
        item_type = request.data.get('item_type')
        if not dept_id or not item_type:
            return Response({"message": "Missing required parameters"}, status=status.HTTP_400_BAD_REQUEST)

        StockItems = StockItem.objects.filter(
            department=dept_id, StockEntryId__item_id__item_type=item_type
        )
        grouped_items = selectors.get_grouped_stock_summary(StockItems)
        grouped_items_list = [
            {
                'item_type': item['StockEntryId__item_id__item_type'],
                'department': DepartmentInfo.objects.get(id=dept_id).name,
                'total_quantity': item['total_quantity'],
            }
            for item in grouped_items
        ]
        first_stock = StockItemSerializer(StockItems.first())
        return Response({'stocks': grouped_items_list, 'first_stock': first_stock.data})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def stock_entry_item_view(request, id):
    designation = str(Designation.objects.get(
        id=HoldsDesignation.objects.select_related('designation').get(id=id).designation_id
    ))
    if str(designation) not in dept_admin_design + ["ps_admin"]:
        return Response({"message": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

    file_id = request.data.get('file_id')
    temp = File.objects.get(id=file_id)
    temp1 = IndentFile.objects.get(file_info=temp)
    stock_entry = StockEntry.objects.get(item_id__indent_file=temp1)
    stocks = StockItem.objects.filter(StockEntryId=stock_entry)
    return Response(StockItemSerializer(stocks, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def stockDelete(request, id):
    designation = str(Designation.objects.get(
        id=HoldsDesignation.objects.select_related('designation').get(id=id).designation_id
    ))
    if str(designation) not in dept_admin_design + ["ps_admin"]:
        return Response({"message": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

    item_id = request.POST.get('id')
    try:
        stock = StockItem.objects.get(id=item_id)
    except StockItem.DoesNotExist:
        return Response({"message": 'Stock item not found', "id": item_id},
                        status=status.HTTP_404_NOT_FOUND)
    stock.delete()
    return Response({"message": "Stock item deleted successfully"}, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def stockTransfer(request, id):
    """
    List available stock items for an indent's item type.
    BUG FIX #1: IndentFile no longer has .item_type directly — get it from IndentItem.
    """
    designation = str(Designation.objects.get(
        id=HoldsDesignation.objects.select_related('designation').get(id=id).designation_id
    ))
    if str(designation) not in dept_admin_design + ["ps_admin"]:
        return Response({"message": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

    item_id = request.POST.get('id')
    temp = File.objects.get(id=item_id)
    indent = IndentFile.objects.get(file_info=temp)

    # BUG FIX #1: item_type is on IndentItem, not IndentFile
    first_item = indent.items.first()
    if not first_item:
        return Response({"message": "No items found for this indent"},
                        status=status.HTTP_400_BAD_REQUEST)
    item_type_required = first_item.item_type

    available = selectors.get_available_items_by_type(item_type_required)
    return Response(StockItemSerializer(available, many=True).data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def performTransfer(request, id):
    designation = str(Designation.objects.get(
        id=HoldsDesignation.objects.select_related('designation').get(id=id).designation_id
    ))
    if str(designation) not in dept_admin_design + ["ps_admin"]:
        return Response({"message": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

    selected_stock_items = request.data.getlist('selected_stock_items[]')
    indent_id = request.data.get('indentId')
    dest_location = request.data.get('dest_location')

    stock_items_list = ast.literal_eval(selected_stock_items[0])

    try:
        transfers = services.perform_stock_transfer(indent_id, stock_items_list, dest_location)
        from .serializers import StockTransferSerializer
        return Response(StockTransferSerializer(transfers, many=True).data,
                        status=status.HTTP_201_CREATED)
    except Exception as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def stockEntry(request, username):
    """
    UC-010: Record a stock entry for an approved indent.
    BUG FIX #2: Pass vendor_id= (not vendor=) to services.create_stock_entry.
    """
    try:
        user = User.objects.get(username=username)
        role = request.POST.get('role', '')

        designation_str = ''
        if role:
            hd = HoldsDesignation.objects.filter(
                user=user, designation__name=role
            ).select_related('designation').first()
            if hd:
                designation_str = hd.designation.name

        if designation_str not in dept_admin_design + ["ps_admin"] and role not in dept_admin_design + ["ps_admin"]:
            return Response({"message": "Not authorized"}, status=status.HTTP_403_FORBIDDEN)

        item_id_val = request.POST.get('id')
        vendor = request.POST.get('vendor')  # may be int id or legacy string
        current_stock = request.POST.get('current_stock')
        received_date = request.POST.get('received_date')
        bill = request.FILES.get('bill')
        location = request.POST.get('location', 'SR1')

        dealing_assistant = request.user.extrainfo

        # BUG FIX #2: parameter is vendor_id, not vendor
        stock_entry = services.create_stock_entry(
            item_id_val=item_id_val,
            vendor_id=vendor,
            current_stock=current_stock,
            recieved_date=received_date,
            bill=bill,
            location=location,
            dealing_assistant=dealing_assistant,
        )
        return Response(StockEntrySerializer(stock_entry).data, status=status.HTTP_201_CREATED)

    except ValidationError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        logger.error("stockEntry error: %s", exc)
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


# ===========================================================================
# T-02: CANCEL INDENT (BR-PS-007, BR-PS-011)
# BUG FIX #3: This view was missing from original submission but referenced in urls.py
# ===========================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def cancel_indent_view(request):
    """
    T-02 (BR-PS-007, BR-PS-011): Cancel an indent with a mandatory reason.
    The record is preserved for audit — not deleted.
    POST body: { file_id, cancellation_reason }
    """
    file_id = request.data.get('file_id')
    cancellation_reason = request.data.get('cancellation_reason', '').strip()

    if not file_id:
        return Response({'error': 'file_id is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        indent = services.cancel_indent(file_id, cancellation_reason, request.user)
        return Response({
            'message': 'Indent cancelled. Record preserved for audit (BR-PS-011).',
            'status': indent.status,
            'cancellation_reason': indent.cancellation_reason,
        }, status=status.HTTP_200_OK)
    except ValidationError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except IndentFile.DoesNotExist:
        return Response({'error': 'Indent not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as exc:
        logger.error("cancel_indent_view error: %s", exc)
        return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ===========================================================================
# T-03: REJECT INDENT (BR-PS-005)
# ===========================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reject_indent_view(request):
    """
    T-03 (BR-PS-005): Reject an indent with a mandatory reason.
    POST body: { file_id, rejection_reason }
    """
    file_id = request.data.get('file_id')
    rejection_reason = request.data.get('rejection_reason', '').strip()

    if not file_id:
        return Response({'error': 'file_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    if not rejection_reason:
        return Response({'error': 'Rejection reason is mandatory (BR-PS-005).'},
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        indent = services.reject_indent(file_id, rejection_reason, request.user)
        return Response({
            'message': 'Indent rejected. Requestor will be notified.',
            'status': indent.status,
            'rejection_reason': indent.rejection_reason,
        }, status=status.HTTP_200_OK)
    except ValidationError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except IndentFile.DoesNotExist:
        return Response({'error': 'Indent not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as exc:
        logger.error("reject_indent_view error: %s", exc)
        return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ===========================================================================
# T-05: DELIVERY CONFIRMATION & DISCREPANCY (WF-002)
# ===========================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def confirm_delivery_view(request):
    """
    T-05 (BR-PS-009, WF-002): Confirm delivery — GRN → Confirmed.
    POST body: { file_id }
    """
    file_id = request.data.get('file_id')
    if not file_id:
        return Response({'error': 'file_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        grn = services.confirm_delivery(file_id, request.user)
        return Response({
            'message': 'Delivery confirmed. GRN updated.',
            'grn': GRNSerializer(grn).data,
        }, status=status.HTTP_200_OK)
    except ValidationError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except GRN.DoesNotExist:
        return Response({'error': 'GRN not found. Has stock been entered for this indent?'},
                        status=status.HTTP_404_NOT_FOUND)
    except Exception as exc:
        logger.error("confirm_delivery_view error: %s", exc)
        return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def report_discrepancy_view(request):
    """
    T-05 (WF-002): Report a discrepancy or reject goods.
    POST body: { file_id, discrepancy_note }
    """
    file_id = request.data.get('file_id')
    discrepancy_note = request.data.get('discrepancy_note', '').strip()

    if not file_id:
        return Response({'error': 'file_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        grn = services.report_discrepancy(file_id, discrepancy_note, request.user)
        return Response({
            'message': 'Discrepancy/rejection recorded.',
            'grn': GRNSerializer(grn).data,
        }, status=status.HTTP_200_OK)
    except ValidationError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as exc:
        logger.error("report_discrepancy_view error: %s", exc)
        return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ===========================================================================
# T-06: INVOICE VERIFICATION (BR-PS-017, BR-PS-018)
# ===========================================================================

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_invoice_view(request, invoice_id):
    """
    T-06 (BR-PS-017, BR-PS-018): 3-way match invoice verification.
    """
    try:
        invoice = services.verify_invoice(invoice_id, request.user)
        from .serializers import InvoiceSerializer
        return Response({
            'message': '3-Way match successful. Invoice verified.',
            'invoice': InvoiceSerializer(invoice).data,
        }, status=status.HTTP_200_OK)
    except ValidationError as exc:
        return Response({'error': str(exc), 'status': 'OnHold'},
                        status=status.HTTP_400_BAD_REQUEST)
    except Invoice.DoesNotExist:
        return Response({'error': 'Invoice not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as exc:
        logger.error("verify_invoice_view error: %s", exc)
        return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ===========================================================================
# T-07: VENDOR MANAGEMENT (BR-PS-012, UC-019)
# ===========================================================================

@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def vendor_list_create(request):
    """
    GET  — list all vendors.
    POST — create a new vendor (starts as Pending).
    """
    if request.method == 'GET':
        vendors = selectors.get_all_vendors()
        return Response(VendorSerializer(vendors, many=True).data)

    elif request.method == 'POST':
        try:
            vendor = services.create_vendor(
                name=request.data.get('name', ''),
                gst_number=request.data.get('gst_number', ''),
                bank_account=request.data.get('bank_account', ''),
                ifsc_code=request.data.get('ifsc_code', ''),
                poc_name=request.data.get('poc_name', ''),
                poc_email=request.data.get('poc_email', ''),
                poc_phone=request.data.get('poc_phone', ''),
            )
            return Response(VendorSerializer(vendor).data, status=status.HTTP_201_CREATED)
        except ValidationError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def verify_vendor_view(request, vendor_id):
    """
    T-07 (BR-PS-012): PS Admin verifies a vendor.
    """
    # Only ps_admin can verify vendors
    is_ps_admin = HoldsDesignation.objects.filter(
        user=request.user, designation__name='ps_admin'
    ).exists()
    if not is_ps_admin:
        return Response({'error': 'Only PS Admin can verify vendors (BR-PS-012).'},
                        status=status.HTTP_403_FORBIDDEN)
    try:
        vendor = services.verify_vendor(vendor_id, request.user)
        return Response({
            'message': f"Vendor '{vendor.name}' verified successfully.",
            'vendor': VendorSerializer(vendor).data,
        }, status=status.HTTP_200_OK)
    except ValidationError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except Vendor.DoesNotExist:
        return Response({'error': 'Vendor not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as exc:
        return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)