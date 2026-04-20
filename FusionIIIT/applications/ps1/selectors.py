"""
selectors.py — PS1 Purchase & Store Module
==========================================
All ORM read-queries live here.  Views must NOT call the ORM directly.
This ensures views stay thin and testable.
"""
from django.db.models import Q, Count, Sum
from applications.ps1.models import (
    IndentFile, IndentItem, StockEntry, StockItem,
    StockTransfer, GRN, PurchaseOrder, Invoice, Vendor, StockReservation,
)
from applications.filetracking.models import File, Tracking
from applications.globals.models import ExtraInfo, HoldsDesignation, Designation


# ---------------------------------------------------------------------------
# IndentFile selectors
# ---------------------------------------------------------------------------

def get_indent_by_file_id(file_id):
    """Return IndentFile whose file_info_id == file_id, or raise DoesNotExist."""
    return IndentFile.objects.select_related('file_info__uploader__department').get(
        file_info_id=file_id
    )


def get_all_filed_indents():
    return (
        IndentFile.objects
        .select_related('file_info__uploader__department')
        .prefetch_related('items')
    )


def get_indents_for_department(department):
    return (
        IndentFile.objects
        .select_related('file_info__uploader__department')
        .prefetch_related('items')
        .filter(file_info__uploader__department=department)
    )


def get_indents_uploaded_by_user(user):
    return (
        IndentFile.objects
        .select_related('file_info__uploader__department')
        .prefetch_related('items')
        .filter(file_info__uploader__user=user)
    )


def get_draft_indents_for_user(user):
    """Return IndentFile records that are still in draft (no Tracking entry)."""
    uploaded = (
        IndentFile.objects
        .filter(file_info__uploader__user=user)
        .select_related('file_info')
    )
    filed_ids = set(
        Tracking.objects.filter(file_id__in=[i.file_info_id for i in uploaded])
        .values_list('file_id', flat=True)
    )
    return [i for i in uploaded if i.file_info_id not in filed_ids]


def get_my_indents(user, page=1, page_size=20):
    """Paginated list of all indents ever filed/created by a user — T-01."""
    qs = (
        IndentFile.objects
        .filter(file_info__uploader__user=user)
        .select_related('file_info')
        .prefetch_related('items')
        .order_by('-file_info__upload_date')
    )
    start = (page - 1) * page_size
    return qs[start: start + page_size]


def get_duplicates_for_intent(indent):
    """
    T-10 (BR-PS-015): find indents with the same item_type in same dept
    that are not cancelled/rejected/paid.
    """
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
    return duplicates


# ---------------------------------------------------------------------------
# StockEntry selectors
# ---------------------------------------------------------------------------

def get_stock_entry_by_indent(indent_file):
    """Return StockEntry for the given IndentFile."""
    return StockEntry.objects.select_related('item_id__indent_file').get(
        item_id__indent_file=indent_file
    )


def get_stock_entries_for_department(department):
    return StockEntry.objects.filter(
        item_id__indent_file__file_info__uploader__department=department
    ).select_related('item_id__indent_file')


def get_all_stock_entries():
    return StockEntry.objects.select_related('item_id__indent_file').all()


def get_available_items_by_type(item_type):
    """T-13: Return StockItems not in use, of the given item_type."""
    return StockItem.objects.filter(
        StockEntryId__item_id__item_type=item_type,
        inUse=False,
    ).select_related('StockEntryId', 'department')


def get_stock_items_for_entry(stock_entry):
    return StockItem.objects.filter(StockEntryId=stock_entry).select_related('department')


def get_stock_items_for_department(department):
    return StockItem.objects.filter(department=department).select_related('department')


def get_all_stock_items():
    return StockItem.objects.all().select_related('department')


def get_grouped_stock_summary(stock_qs):
    return (
        stock_qs
        .values('StockEntryId__item_id__item_type', 'department')
        .annotate(total_quantity=Count('id'))
    )


# ---------------------------------------------------------------------------
# File / Tracking selectors
# ---------------------------------------------------------------------------

def get_file_by_id(file_id):
    return File.objects.select_related('uploader__user', 'uploader__department').get(pk=file_id)


def get_tracking_for_file(file_obj):
    return (
        Tracking.objects
        .select_related(
            'file_id__uploader__user',
            'file_id__uploader__department',
            'file_id__designation',
            'current_id',
            'current_design',
            'receiver_id',
            'receive_design',
        )
        .filter(file_id=file_obj)
    )


# ---------------------------------------------------------------------------
# User / Designation selectors
# ---------------------------------------------------------------------------

def get_holds_designation_by_id(hd_id):
    return HoldsDesignation.objects.select_related('user', 'designation', 'working').get(id=hd_id)


def get_designation_name(hd_id):
    hd = HoldsDesignation.objects.select_related('designation').get(id=hd_id)
    return str(hd.designation)


def get_user_suggestions(limit=200):
    """T-09: Returns only staff users (no hardcoded usernames)."""
    return (
        ExtraInfo.objects
        .filter(user_type__in=['staff', 'Faculty'])
        .select_related('user', 'department')
        .values('user__username', 'user__first_name', 'user__last_name', 'department__name')[:limit]
    )


# ---------------------------------------------------------------------------
# Vendor selectors (T-07)
# ---------------------------------------------------------------------------

def get_all_vendors():
    return Vendor.objects.order_by('name')


def get_vendor_by_id(vendor_id):
    return Vendor.objects.get(pk=vendor_id)


def get_verified_vendor(vendor_id):
    return Vendor.objects.get(pk=vendor_id, verification_status='Verified')


# ---------------------------------------------------------------------------
# GRN selectors (T-05)
# ---------------------------------------------------------------------------

def get_or_create_grn(indent_file):
    grn, _ = GRN.objects.get_or_create(indent_file=indent_file)
    return grn


# ---------------------------------------------------------------------------
# Invoice / PurchaseOrder selectors (T-06)
# ---------------------------------------------------------------------------

def get_purchase_order_by_indent(indent_file):
    return PurchaseOrder.objects.get(indent_file=indent_file)


def get_invoices_for_po(po):
    return Invoice.objects.filter(purchase_order=po)


# ---------------------------------------------------------------------------
# StockReservation selectors (T-12)
# ---------------------------------------------------------------------------

def get_active_reservations_for_indent(indent_file):
    return StockReservation.objects.filter(
        indent_file=indent_file, status='Active'
    ).select_related('stock_item')
