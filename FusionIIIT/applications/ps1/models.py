from django.db import models
from django.contrib.auth.models import User
from applications.globals.models import Staff, ExtraInfo, DepartmentInfo
from applications.filetracking.models import File
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


class LocationChoices(models.TextChoices):
    LHTC = 'SR1', 'LHTC'
    COMPUTER_CENTER = 'SR2', 'Computer Center'
    PANINI_HOSTEL = 'SR3', 'Panini Hostel'
    LAB_COMPLEX = 'SR4', 'Lab complex'
    ADMIN_BLOCK = 'SR5', 'Admin Block'


def make_location_field(default=LocationChoices.LHTC):
    return models.CharField(max_length=100, choices=LocationChoices.choices, default=default)


class Constants:
    Locations = LocationChoices.choices


# ---------------------------------------------------------------------------
# T-08: IndentStatus TextChoices (BR-PS-007, UC-003)
# ---------------------------------------------------------------------------
class IndentStatus(models.TextChoices):
    DRAFT = 'Draft', 'Draft'
    SUBMITTED = 'Submitted', 'Submitted'
    IN_REVIEW = 'InReview', 'In Review'
    PENDING_CLARIFICATION = 'PendingClarification', 'Pending Clarification'
    APPROVED_INTERNAL = 'ApprovedInternal', 'Approved – Internal Issue'
    APPROVED_EXTERNAL = 'ApprovedExternal', 'Approved – External Procurement'
    PO_ISSUED = 'POIssued', 'PO Issued'
    DISPATCHED = 'Dispatched', 'Dispatched'
    RECEIVED = 'Received', 'Received'
    INVOICE_VERIFIED = 'InvoiceVerified', 'Invoice Verified'
    PAID = 'Paid', 'Paid'
    REJECTED = 'Rejected', 'Rejected'
    CANCELLED = 'Cancelled', 'Cancelled'
    PENDING_WITHDRAWAL = 'PendingWithdrawal', 'Pending Withdrawal'
    WITHDRAWN = 'Withdrawn', 'Withdrawn'


# Modifiable states: only these allow cancellation/editing (BR-PS-007)
MODIFIABLE_STATUSES = {
    IndentStatus.SUBMITTED,
    IndentStatus.IN_REVIEW,
    IndentStatus.PENDING_CLARIFICATION,
    IndentStatus.PENDING_WITHDRAWAL,
}


# ---------------------------------------------------------------------------
# T-07: Vendor model — replaces free-text CharField (BR-PS-012, UC-019)
# ---------------------------------------------------------------------------
class Vendor(models.Model):
    name = models.CharField(max_length=250, blank=False)
    gst_number = models.CharField(max_length=15, blank=True, default='')
    bank_account = models.CharField(max_length=30, blank=True, default='')
    ifsc_code = models.CharField(max_length=11, blank=True, default='')
    poc_name = models.CharField(max_length=250, blank=True, default='')
    poc_email = models.EmailField(blank=True, default='')
    poc_phone = models.CharField(max_length=15, blank=True, default='')
    verification_status = models.CharField(
        max_length=20,
        choices=[('Pending', 'Pending'), ('Verified', 'Verified'), ('Blocked', 'Blocked')],
        default='Pending',
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'Vendor'

    def __str__(self):
        return f"{self.name} ({self.verification_status})"

    @property
    def is_verified(self):
        return self.verification_status == 'Verified'


# ---------------------------------------------------------------------------
# T-04: ApprovalThreshold model — BR-PS-004 cost-based routing
# ---------------------------------------------------------------------------
class ApprovalThreshold(models.Model):
    cost_limit = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='Upper inclusive limit. NULL = catch-all.'
    )
    required_designation = models.CharField(max_length=250)
    description = models.CharField(max_length=500, blank=True)

    class Meta:
        db_table = 'ApprovalThreshold'
        ordering = ['cost_limit']


# ---------------------------------------------------------------------------
# IndentFile — T-08 status, T-02 cancellation_reason, T-03 rejection_reason
# ---------------------------------------------------------------------------
class IndentFile(models.Model):
    file_info = models.OneToOneField(File, on_delete=models.CASCADE, primary_key=True)
    indent_name = models.CharField(max_length=250, blank=False, default='Untitled Indent')
    description = models.TextField(blank=True)
    head_approval = models.BooleanField(default=False)
    director_approval = models.BooleanField(default=False)
    financial_approval = models.BooleanField(default=False)
    purchased = models.BooleanField(default=False)
    approved_by = models.TextField(blank=True, default="")

    # T-08
    status = models.CharField(
        max_length=30, choices=IndentStatus.choices, default=IndentStatus.SUBMITTED
    )
    # T-02 (BR-PS-007, BR-PS-011)
    cancellation_reason = models.TextField(blank=True, default='')
    # T-03 (BR-PS-005)
    rejection_reason = models.TextField(blank=True, default='')
    # T-10 (BR-PS-015)
    is_duplicate_flag = models.BooleanField(default=False)
    # T-11 (BR-PS-003)
    internal_issue_flag = models.BooleanField(default=False)

    class Meta:
        db_table = 'IndentFile'


class IndentItem(models.Model):
    indent_file = models.ForeignKey(IndentFile, on_delete=models.CASCADE, related_name='items')
    item_name = models.CharField(max_length=250, blank=False)
    quantity = models.IntegerField(blank=False)
    present_stock = models.IntegerField(blank=False)
    estimated_cost = models.IntegerField(null=True, blank=False)
    purpose = models.CharField(max_length=250, blank=False)
    specification = models.CharField(max_length=250)
    item_type = models.CharField(max_length=250)
    item_subtype = models.CharField(max_length=250, blank=False, default='computers')
    nature = models.BooleanField(default=False)
    indigenous = models.BooleanField(default=False)
    replaced = models.BooleanField(default=False)
    budgetary_head = models.CharField(max_length=250)
    expected_delivery = models.DateField(blank=False)
    sources_of_supply = models.CharField(max_length=250)

    class Meta:
        db_table = 'IndentItem'


class StockEntry(models.Model):
    item_id = models.OneToOneField(IndentItem, on_delete=models.CASCADE, primary_key=True)
    dealing_assistant_id = models.ForeignKey(ExtraInfo, on_delete=models.CASCADE)
    # T-07: ForeignKey to Vendor (BR-PS-012). Legacy text field kept for migration.
    vendor = models.ForeignKey(
        Vendor, on_delete=models.PROTECT, null=True, blank=True,
        help_text='Must be a verified Vendor (BR-PS-012).'
    )
    vendor_legacy = models.CharField(max_length=250, blank=True, default='',
                                     help_text='Deprecated free-text vendor. Use FK.')
    current_stock = models.IntegerField(blank=False)
    recieved_date = models.DateField(blank=False)
    bill = models.FileField(blank=False)
    location = make_location_field()

    class Meta:
        db_table = 'StockEntry'


class StockItem(models.Model):
    StockEntryId = models.ForeignKey(StockEntry, on_delete=models.CASCADE)
    nomenclature = models.CharField(max_length=100, unique=True)
    inUse = models.BooleanField(default=True)
    department = models.ForeignKey(DepartmentInfo, on_delete=models.CASCADE, null=True, blank=True)
    location = make_location_field()
    isTransferred = models.BooleanField(default=False)

    class Meta:
        db_table = 'StockItem'

    def save(self, *args, **kwargs):
        if not self.nomenclature:
            max_existing_number = StockItem.objects.filter(StockEntryId=self.StockEntryId_id).count()
            new_number = max_existing_number + 1
            self.nomenclature = f"{self.StockEntryId.item_id}_{new_number}"
        super().save(*args, **kwargs)


class StockTransfer(models.Model):
    indent_file = models.ForeignKey(IndentFile, on_delete=models.CASCADE)
    src_dept = models.ForeignKey(DepartmentInfo, on_delete=models.CASCADE, null=True, blank=True,
                                 related_name='dept_src_transfers')
    dest_dept = models.ForeignKey(DepartmentInfo, on_delete=models.CASCADE, null=True, blank=True,
                                  related_name='dept_dest_transfers')
    stockItem = models.ForeignKey(StockItem, on_delete=models.CASCADE)
    src_location = make_location_field(default=LocationChoices.LHTC)
    dest_location = make_location_field(default=LocationChoices.COMPUTER_CENTER)
    dateTime = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'StockTransfer'


# ---------------------------------------------------------------------------
# T-05: GRN model — delivery confirmation (BR-PS-009, WF-002)
# ---------------------------------------------------------------------------
class GRN(models.Model):
    class GRNStatus(models.TextChoices):
        PENDING = 'Pending', 'Pending Confirmation'
        CONFIRMED = 'Confirmed', 'Confirmed – All OK'
        DISCREPANCY = 'Discrepancy', 'Discrepancy Reported'
        REJECTED = 'Rejected', 'Goods Rejected / Damaged'

    indent_file = models.OneToOneField(IndentFile, on_delete=models.CASCADE, related_name='grn')
    confirmed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    status = models.CharField(max_length=20, choices=GRNStatus.choices, default=GRNStatus.PENDING)
    confirmation_date = models.DateTimeField(null=True, blank=True)
    discrepancy_note = models.TextField(blank=True, default='')
    rejection_reason = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'GRN'


# ---------------------------------------------------------------------------
# T-06: PurchaseOrder & Invoice — 3-way match (BR-PS-017, BR-PS-018)
# ---------------------------------------------------------------------------
class PurchaseOrder(models.Model):
    class POStatus(models.TextChoices):
        DRAFT = 'Draft', 'Draft'
        ISSUED = 'Issued', 'Issued'
        RECEIVED = 'Received', 'Received'
        CANCELLED = 'Cancelled', 'Cancelled'

    indent_file = models.OneToOneField(IndentFile, on_delete=models.CASCADE, related_name='purchase_order')
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, null=True, blank=True)
    po_number = models.CharField(max_length=50, unique=True)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=POStatus.choices, default=POStatus.DRAFT)
    issued_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    issued_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'PurchaseOrder'


class Invoice(models.Model):
    class InvoiceStatus(models.TextChoices):
        PENDING = 'Pending', 'Pending Verification'
        VERIFIED = 'Verified', 'Verified – 3-Way Match OK'
        ON_HOLD = 'OnHold', 'On Hold – Mismatch'
        REJECTED = 'Rejected', 'Rejected'
        PAID = 'Paid', 'Paid'

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='invoices')
    invoice_number = models.CharField(max_length=50)
    invoice_amount = models.DecimalField(max_digits=12, decimal_places=2)
    status = models.CharField(max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.PENDING)
    verified_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    verification_date = models.DateTimeField(null=True, blank=True)
    mismatch_reason = models.TextField(blank=True, default='')
    invoice_file = models.FileField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'Invoice'


# ---------------------------------------------------------------------------
# T-12: StockReservation — BR-PS-013
# ---------------------------------------------------------------------------
class StockReservation(models.Model):
    class ReservationStatus(models.TextChoices):
        ACTIVE = 'Active', 'Active'
        FULFILLED = 'Fulfilled', 'Fulfilled'
        RELEASED = 'Released', 'Released'
        EXPIRED = 'Expired', 'Expired'

    indent_file = models.ForeignKey(IndentFile, on_delete=models.CASCADE, related_name='reservations')
    stock_item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name='reservations')
    reserved_qty = models.IntegerField(default=1)
    status = models.CharField(max_length=20, choices=ReservationStatus.choices,
                               default=ReservationStatus.ACTIVE)
    created_at = models.DateTimeField(default=timezone.now)
    expiry = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'StockReservation'


@receiver(post_save, sender=StockEntry)
def create_stock_items(sender, instance, created, **kwargs):
    if created:
        department = instance.item_id.indent_file.file_info.uploader.department
        current_stock = int(instance.current_stock)
        for _ in range(current_stock):
            StockItem.objects.create(
                StockEntryId=instance,
                location=instance.location,
                department=department,
            )