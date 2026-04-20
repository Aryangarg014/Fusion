"""
services.py — PS1 Purchase & Store Module Business Logic
=========================================================
All write-operations (state changes, creations, validations) live here.
Views call services; services call the ORM directly or via selectors.
No HTTP-layer code in this file.
"""
import logging
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import transaction
from django.contrib.auth.models import User

from applications.ps1.models import (
    IndentFile, IndentItem, StockEntry, StockItem, StockTransfer,
    GRN, PurchaseOrder, Invoice, Vendor, StockReservation,
    MODIFIABLE_STATUSES, IndentStatus,
)
from applications.filetracking.models import File, Tracking
from applications.filetracking.sdk.methods import (
    create_draft, create_file, forward_file, archive_file, view_inbox,
    view_outbox, view_archived,
)
from applications.globals.models import (
    ExtraInfo, HoldsDesignation, Designation, DepartmentInfo,
)
from django.shortcuts import get_object_or_404

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Role constants — defined here to avoid circular imports from views.py
# ---------------------------------------------------------------------------
DEPT_ADMIN_ROLES = [
    "deptadmin_cse", "deptadmin_ece", "deptadmin_me",
    "deptadmin_sm", "deptadmin_design", "deptadmin_liberalarts", "deptadmin_ns",
]

DEPT_ADMIN_TO_DEPT = {
    "deptadmin_cse": "CSE",
    "deptadmin_ece": "ECE",
    "deptadmin_me": "ME",
    "deptadmin_sm": "SM",
    "deptadmin_design": "Design",
    "deptadmin_liberalarts": "Liberal Arts",
    "deptadmin_ns": "Natural Science",
}


# ===========================================================================
# INDENT SERVICES
# ===========================================================================

def create_proposal(uploader_user, title, description, upload_file,
                    items_data, designation_obj, receiver_user=None,
                    receiver_designation=None, remarks=''):
    """
    UC-001: File a new indent proposal.
    T-01: Validates required fields server-side (BR-PS-001).
    T-10: Duplicate detection (BR-PS-015).
    """
    # BR-PS-001: server-side validation
    if not title or not title.strip():
        raise ValidationError("Indent title is required (BR-PS-001).")
    if not items_data:
        raise ValidationError("At least one item is required (BR-PS-001).")

    with transaction.atomic():
        if receiver_user is None or receiver_designation is None:
            raise ValidationError("Receiver and receiver designation are required for proposal submission.")

        file_id = create_file(
            uploader=uploader_user.username,
            uploader_designation=designation_obj,
            receiver=receiver_user.username,
            receiver_designation=receiver_designation.name,
            src_module="ps1",
            src_object_id="",
            file_extra_JSON={"value": 1},
            attached_file=upload_file,
            subject=title,
            description=description,
        )
        file_obj = File.objects.get(pk=file_id)
        indent = IndentFile.objects.create(
            file_info=file_obj,
            indent_name=title,
            description=description or '',
            status=IndentStatus.SUBMITTED,
        )
        for item_data in items_data:
            IndentItem.objects.create(indent_file=indent, **item_data)

        # Keep the submission audit trail aligned with the forward target.
        if remarks:
            latest_tracking = Tracking.objects.filter(file_id=file_obj).order_by('-forward_date').first()
            if latest_tracking:
                latest_tracking.remarks = remarks
                latest_tracking.save(update_fields=['remarks'])

        # T-10 (BR-PS-015): flag if possible duplicate
        _flag_duplicate_if_needed(indent)

        return indent


def create_draft_indent(uploader_user, title, description, upload_file,
                        items_data, designation_obj):
    """
    UC-002: Save an indent as draft (not filed yet — no Tracking entry).
    """
    if not title or not title.strip():
        raise ValidationError("Draft title is required.")

    with transaction.atomic():
        file_id = create_draft(
            uploader=uploader_user.username,
            uploader_designation=designation_obj,
            src_module="ps1",
            src_object_id="",
            file_extra_JSON={"value": 2},
            attached_file=upload_file,
        )
        file_obj = File.objects.get(pk=file_id)
        indent = IndentFile.objects.create(
            file_info=file_obj,
            indent_name=title,
            description=description or '',
            status=IndentStatus.DRAFT,
        )
        if items_data:
            for item_data in items_data:
                IndentItem.objects.create(indent_file=indent, **item_data)
        return indent


def forward_indent(file_id, sender_hd_id, receiver_hd_id, remarks, upload_file=None):
    """
    UC-004: Forward an indent to the next approver.
    T-04 (BR-PS-004): Cost threshold routing is enforced separately in the view.
    """
    indent = IndentFile.objects.get(file_info_id=file_id)
    file_obj = indent.file_info

    sender_hd = HoldsDesignation.objects.select_related('designation').get(id=sender_hd_id)
    sender_designation_name = sender_hd.designation.name

    receiver_hd = HoldsDesignation.objects.select_related('user', 'designation').get(id=receiver_hd_id)
    receiver_user = receiver_hd.user
    receive_designation = receiver_hd.designation

    with transaction.atomic():
        forward_file(
            file_id=file_obj.id,
            receiver=receiver_user,
            receiver_designation=receive_designation,
            file_extra_JSON={"key": 2},
            remarks=remarks,
            file_attachment=upload_file,
        )
        _update_indent_approvals_by_holds(indent, sender_designation_name,
                                          str(receive_designation))
        indent.status = IndentStatus.IN_REVIEW
        indent.save(update_fields=['head_approval', 'director_approval',
                                   'financial_approval', 'status'])
    return indent


def cancel_indent(file_id, cancellation_reason, cancelled_by_user):
    """
    T-02 (BR-PS-007, BR-PS-011): Cancel an indent with mandatory reason.
    """
    indent = IndentFile.objects.get(file_info_id=file_id)

    # BR-PS-007: Only modifiable statuses can be cancelled
    if indent.status not in MODIFIABLE_STATUSES:
        raise ValidationError(
            f"Indent in status '{indent.status}' cannot be cancelled (BR-PS-007). "
            f"Allowed states: {', '.join(MODIFIABLE_STATUSES)}"
        )
    if not cancellation_reason or not cancellation_reason.strip():
        raise ValidationError("Cancellation reason is required (BR-PS-011).")

    indent.status = IndentStatus.CANCELLED
    indent.cancellation_reason = cancellation_reason.strip()
    indent.save(update_fields=['status', 'cancellation_reason'])
    logger.info("Indent %s cancelled by %s: %s", file_id, cancelled_by_user, cancellation_reason)
    return indent


def reject_indent(file_id, rejection_reason, rejected_by_user):
    """
    T-03 (BR-PS-005): Reject an indent with a mandatory reason.
    """
    indent = IndentFile.objects.get(file_info_id=file_id)
    if not rejection_reason or not rejection_reason.strip():
        raise ValidationError("Rejection reason is mandatory (BR-PS-005).")
    indent.status = IndentStatus.REJECTED
    indent.rejection_reason = rejection_reason.strip()
    indent.save(update_fields=['status', 'rejection_reason'])
    logger.info("Indent %s rejected by %s: %s", file_id, rejected_by_user, rejection_reason)
    return indent


def approve_indent(file_id, approved_by_user, approval_data=""):
    """
    UC-007: Approve an indent (head/director/financial approval).
    """
    indent = IndentFile.objects.get(file_info_id=file_id)
    current = indent.approved_by or ""
    indent.approved_by = f"{current}|{approved_by_user.username}:{approval_data}".lstrip("|")
    indent.head_approval = True
    indent.save(update_fields=['approved_by', 'head_approval'])
    return indent


def archive_indent(file_id):
    """UC-014: Archive a processed indent."""
    return archive_file(file_id)


def delete_indent(file_id):
    """
    DEPRECATED: Hard-delete. Use cancel_indent() instead (T-02/BR-PS-011).
    Kept for backward compat with existing frontend.
    """
    indent = IndentFile.objects.get(file_info_id=file_id)
    indent.delete()


# ===========================================================================
# STOCK ENTRY SERVICES
# ===========================================================================

def create_stock_entry(item_id_val, vendor_id, current_stock, recieved_date,
                        bill, location, dealing_assistant):
    """
    UC-010 / T-05: Record stock entry.
    vendor_id may be None (handled via vendor_legacy) or a Vendor PK.
    """
    vendor_obj = None
    vendor_legacy_str = ''

    if vendor_id:
        try:
            vendor_obj = Vendor.objects.get(pk=vendor_id)
            # BR-PS-012: vendor must be verified
            if not vendor_obj.is_verified:
                raise ValidationError(
                    f"Vendor '{vendor_obj.name}' is not verified (BR-PS-012). "
                    "Have PS Admin verify the vendor first."
                )
        except Vendor.DoesNotExist:
            # Treat as legacy free-text
            vendor_legacy_str = str(vendor_id)

    indent_item = IndentItem.objects.get(pk=item_id_val)

    with transaction.atomic():
        stock_entry = StockEntry.objects.create(
            item_id=indent_item,
            dealing_assistant_id=dealing_assistant,
            vendor=vendor_obj,
            vendor_legacy=vendor_legacy_str,
            current_stock=current_stock,
            recieved_date=recieved_date,
            bill=bill,
            location=location,
        )
        # GRN creation is triggered by post_save → StockItems created there
        _create_pending_grn_if_needed(indent_item.indent_file)
    return stock_entry


def _create_pending_grn_if_needed(indent_file):
    """T-05: Ensure a GRN record exists when stock is first entered."""
    GRN.objects.get_or_create(
        indent_file=indent_file,
        defaults={'status': GRN.GRNStatus.PENDING}
    )


# ===========================================================================
# DELIVERY / GRN SERVICES (T-05)
# ===========================================================================

def confirm_delivery(file_id, confirmed_by_user):
    """
    T-05 (BR-PS-009): Confirm goods received — mark GRN Confirmed.
    """
    indent = IndentFile.objects.get(file_info_id=file_id)
    grn = GRN.objects.get(indent_file=indent)

    if grn.status == GRN.GRNStatus.CONFIRMED:
        raise ValidationError("Delivery already confirmed.")

    with transaction.atomic():
        grn.status = GRN.GRNStatus.CONFIRMED
        grn.confirmed_by = confirmed_by_user
        grn.confirmation_date = timezone.now()
        grn.save(update_fields=['status', 'confirmed_by', 'confirmation_date'])

        indent.status = IndentStatus.RECEIVED
        indent.save(update_fields=['status'])

    logger.info("GRN %s confirmed by %s for indent %s", grn.id, confirmed_by_user, file_id)
    return grn


def report_discrepancy(file_id, discrepancy_note, reported_by_user):
    """
    T-05 (WF-002): Report discrepancy on delivery — mark GRN Discrepancy.
    """
    indent = IndentFile.objects.get(file_info_id=file_id)
    grn, _ = GRN.objects.get_or_create(indent_file=indent)

    if not discrepancy_note or not discrepancy_note.strip():
        raise ValidationError("A description of the discrepancy is required.")

    with transaction.atomic():
        if discrepancy_note.startswith("REJECTED"):
            grn.status = GRN.GRNStatus.REJECTED
            grn.rejection_reason = discrepancy_note.strip()
        else:
            grn.status = GRN.GRNStatus.DISCREPANCY
            grn.discrepancy_note = discrepancy_note.strip()
        grn.confirmed_by = reported_by_user
        grn.confirmation_date = timezone.now()
        grn.save(update_fields=['status', 'discrepancy_note', 'rejection_reason',
                                 'confirmed_by', 'confirmation_date'])

    logger.info("Discrepancy reported by %s for indent %s", reported_by_user, file_id)
    return grn


# ===========================================================================
# INVOICE SERVICES (T-06)
# ===========================================================================

def verify_invoice(invoice_id, verified_by_user):
    """
    T-06 (BR-PS-017, BR-PS-018): 3-Way-Match invoice verification.
    Checks: PO amount == Invoice amount AND GRN is Confirmed.
    """
    invoice = Invoice.objects.select_related('purchase_order__indent_file').get(pk=invoice_id)
    po = invoice.purchase_order
    indent = po.indent_file

    # 3-Way match check
    mismatch_reasons = []

    # Check 1: Invoice amount vs PO amount (BR-PS-017)
    if abs(invoice.invoice_amount - po.total_amount) > 0:
        mismatch_reasons.append(
            f"Invoice amount (₹{invoice.invoice_amount}) does not match "
            f"PO amount (₹{po.total_amount}) — BR-PS-017."
        )

    # Check 2: GRN must be Confirmed (BR-PS-018)
    try:
        grn = GRN.objects.get(indent_file=indent)
        if grn.status != GRN.GRNStatus.CONFIRMED:
            mismatch_reasons.append(
                f"GRN is not confirmed (status: {grn.status}) — BR-PS-018. "
                "Confirm delivery before verifying invoice."
            )
    except GRN.DoesNotExist:
        mismatch_reasons.append("No GRN exists for this indent — BR-PS-018.")

    with transaction.atomic():
        if mismatch_reasons:
            invoice.status = Invoice.InvoiceStatus.ON_HOLD
            invoice.mismatch_reason = " | ".join(mismatch_reasons)
            invoice.verified_by = verified_by_user
            invoice.verification_date = timezone.now()
            invoice.save(update_fields=['status', 'mismatch_reason', 'verified_by', 'verification_date'])
            raise ValidationError(" | ".join(mismatch_reasons))
        else:
            invoice.status = Invoice.InvoiceStatus.VERIFIED
            invoice.mismatch_reason = ''
            invoice.verified_by = verified_by_user
            invoice.verification_date = timezone.now()
            invoice.save(update_fields=['status', 'mismatch_reason', 'verified_by', 'verification_date'])

            indent.status = IndentStatus.INVOICE_VERIFIED
            indent.save(update_fields=['status'])

    logger.info("Invoice %s verified by %s", invoice_id, verified_by_user)
    return invoice


# ===========================================================================
# VENDOR SERVICES (T-07)
# ===========================================================================

def create_vendor(name, gst_number='', bank_account='', ifsc_code='',
                  poc_name='', poc_email='', poc_phone=''):
    """UC-019: Register a new vendor. Starts as Pending verification."""
    if not name or not name.strip():
        raise ValidationError("Vendor name is required.")
    vendor = Vendor.objects.create(
        name=name.strip(),
        gst_number=gst_number,
        bank_account=bank_account,
        ifsc_code=ifsc_code,
        poc_name=poc_name,
        poc_email=poc_email,
        poc_phone=poc_phone,
        verification_status='Pending',
    )
    logger.info("Vendor '%s' created (id=%s)", vendor.name, vendor.id)
    return vendor


def verify_vendor(vendor_id, verified_by_user):
    """
    T-07 (BR-PS-012): PS Admin verifies a vendor.
    Only PS Admin should call this (enforced in view).
    """
    vendor = Vendor.objects.get(pk=vendor_id)
    if vendor.verification_status == 'Verified':
        raise ValidationError(f"Vendor '{vendor.name}' is already verified.")
    vendor.verification_status = 'Verified'
    vendor.save(update_fields=['verification_status', 'updated_at'])
    logger.info("Vendor %s verified by %s", vendor_id, verified_by_user)
    return vendor


# ===========================================================================
# STOCK TRANSFER SERVICES (T-13)
# ===========================================================================

def perform_stock_transfer(indent_file_id, stock_item_ids, dest_location):
    """
    UC-021 / WF-003: Inter-departmental stock transfer.
    T-12 (BR-PS-013): Updates StockReservations.
    """
    my_indent = IndentFile.objects.select_related(
        'file_info__uploader__department'
    ).get(file_info_id=indent_file_id)
    dest_dept = my_indent.file_info.uploader.department

    total_qty_needed = (
        my_indent.items.aggregate(total=__import__('django.db.models', fromlist=['Sum']).Sum('quantity'))
        .get('total') or 0
    )
    more_required = total_qty_needed - len(stock_item_ids)

    transfers = []
    with transaction.atomic():
        for item_id in stock_item_ids:
            stock_item = StockItem.objects.select_related('department').get(id=item_id)
            src_dept = stock_item.department
            src_location = stock_item.location

            stock_item.department = dest_dept
            stock_item.location = dest_location
            stock_item.inUse = True
            stock_item.isTransferred = True
            stock_item.save()

            transfer = StockTransfer.objects.create(
                indent_file=my_indent,
                src_dept=src_dept,
                dest_dept=dest_dept,
                stockItem=stock_item,
                src_location=src_location,
                dest_location=dest_location,
            )
            transfers.append(transfer)
            # T-12: fulfill any active reservation for this item
            StockReservation.objects.filter(
                indent_file=my_indent, stock_item=stock_item, status='Active'
            ).update(status='Fulfilled')

        if more_required == 0:
            my_indent.purchased = True
        else:
            my_indent.items.update(quantity=more_required)
        my_indent.save(update_fields=['purchased'])

    return transfers


def reserve_stock(indent_file_id, stock_item_id, qty=1, expiry_hours=48):
    """T-12 (BR-PS-013): Reserve a stock item for an approved indent."""
    indent = IndentFile.objects.get(file_info_id=indent_file_id)
    stock_item = StockItem.objects.get(pk=stock_item_id)

    if not stock_item.inUse is False:
        raise ValidationError("Stock item is already in use (BR-PS-013).")

    expiry = timezone.now() + timedelta(hours=expiry_hours)
    reservation = StockReservation.objects.create(
        indent_file=indent,
        stock_item=stock_item,
        reserved_qty=qty,
        status='Active',
        expiry=expiry,
    )
    stock_item.inUse = True
    stock_item.save(update_fields=['inUse'])
    return reservation


# ===========================================================================
# INTERNAL HELPERS
# ===========================================================================

def _update_indent_approvals_by_holds(indent, sender_name, receive_design_str):
    """
    Update approval flags based on who is forwarding to whom.
    FIX: Uses DEPT_ADMIN_ROLES defined in this module instead of importing
         from api.views (which would create a circular import).
    """
    if receive_design_str in DEPT_ADMIN_ROLES:
        indent.head_approval = True
    elif (
        (sender_name in DEPT_ADMIN_ROLES or sender_name == "ps_admin")
        and receive_design_str == "Accounts Admin"
    ):
        indent.director_approval = True
        indent.financial_approval = True
        indent.head_approval = True


def _flag_duplicate_if_needed(indent):
    """T-10 (BR-PS-015): Flag indent if a similar one already exists in same dept."""
    dept = indent.file_info.uploader.department
    item_types = list(indent.items.values_list('item_type', flat=True))
    duplicates = (
        IndentFile.objects
        .filter(
            file_info__uploader__department=dept,
            items__item_type__in=item_types,
        )
        .exclude(pk=indent.pk)
        .exclude(status__in=['Cancelled', 'Rejected', 'Paid', 'Draft'])
        .distinct()
    )
    if duplicates.exists():
        indent.is_duplicate_flag = True
        indent.save(update_fields=['is_duplicate_flag'])
