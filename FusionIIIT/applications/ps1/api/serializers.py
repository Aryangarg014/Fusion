"""
api/serializers.py — PS1 Purchase & Store Module
=================================================
A-6: All serializers use explicit field lists (not '__all__')
     to prevent mass-assignment vulnerabilities.
"""
from rest_framework import serializers  # type: ignore
from applications.ps1.models import (
    IndentFile, IndentItem, StockEntry, StockItem, StockTransfer,
    GRN, PurchaseOrder, Invoice, Vendor, StockReservation,
)
from applications.globals.models import ExtraInfo, HoldsDesignation
from applications.filetracking.models import File, Tracking


class FileSerializer(serializers.ModelSerializer):
    class Meta:
        model = File
        fields = [
            'id', 'subject', 'upload_date', 'uploader',
            'designation', 'src_module', 'src_object_id',
            'file_extra_JSON', 'attached_file',
        ]


class IndentItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = IndentItem
        fields = [
            'id', 'indent_file', 'item_name', 'quantity',
            'present_stock', 'estimated_cost', 'purpose',
            'specification', 'item_type', 'item_subtype',
            'nature', 'indigenous', 'replaced', 'budgetary_head',
            'expected_delivery', 'sources_of_supply',
        ]


class IndentFileSerializer(serializers.ModelSerializer):
    items = IndentItemSerializer(many=True, read_only=True)

    class Meta:
        model = IndentFile
        fields = [
            'file_info', 'indent_name', 'description',
            'head_approval', 'director_approval', 'financial_approval',
            'purchased', 'approved_by',
            # T-08: new status/flag fields
            'status', 'cancellation_reason', 'rejection_reason',
            'is_duplicate_flag', 'internal_issue_flag',
            'items',
        ]


class ExtraInfoSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtraInfo
        fields = [
            'id', 'user', 'user_type', 'department',
            'title', 'sex', 'date_of_birth', 'profile_picture',
            'phone_no', 'address', 'about_me',
        ]


class HoldsDesignationSerializer(serializers.ModelSerializer):
    class Meta:
        model = HoldsDesignation
        fields = ['id', 'user', 'designation', 'working']


class TrackingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tracking
        fields = [
            'id', 'file_id', 'current_id', 'current_design',
            'receiver_id', 'receive_design', 'remarks',
            'upload_date', 'file_attachment',
        ]


class StockEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = StockEntry
        fields = [
            'item_id', 'dealing_assistant_id', 'vendor', 'vendor_legacy',
            'current_stock', 'recieved_date', 'bill', 'location',
        ]


class StockItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockItem
        fields = [
            'id', 'StockEntryId', 'nomenclature',
            'inUse', 'department', 'location', 'isTransferred',
        ]


class StockTransferSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockTransfer
        fields = [
            'id', 'indent_file', 'src_dept', 'dest_dept',
            'stockItem', 'src_location', 'dest_location', 'dateTime',
        ]


# ---------------------------------------------------------------------------
# T-05, T-06, T-07 New serializers
# ---------------------------------------------------------------------------

class GRNSerializer(serializers.ModelSerializer):
    class Meta:
        model = GRN
        fields = [
            'id', 'indent_file', 'confirmed_by', 'status',
            'confirmation_date', 'discrepancy_note', 'rejection_reason', 'created_at',
        ]


class PurchaseOrderSerializer(serializers.ModelSerializer):
    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'indent_file', 'vendor', 'po_number',
            'total_amount', 'status', 'issued_by', 'issued_date', 'created_at',
        ]


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = [
            'id', 'purchase_order', 'invoice_number', 'invoice_amount',
            'status', 'verified_by', 'verification_date', 'mismatch_reason', 'created_at',
        ]


class VendorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vendor
        fields = [
            'id', 'name', 'gst_number', 'bank_account', 'ifsc_code',
            'poc_name', 'poc_email', 'poc_phone', 'verification_status',
            'created_at', 'updated_at',
        ]


class StockReservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockReservation
        fields = [
            'id', 'indent_file', 'stock_item', 'reserved_qty', 'status', 'created_at', 'expiry',
        ]
