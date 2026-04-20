"""
0002_assignment7_sprint.py — Migration for PS1 Assignment 7 Sprint
Adds: status/cancellation_reason/rejection_reason/is_duplicate_flag/internal_issue_flag
      fields to IndentFile; Vendor model; ApprovalThreshold model; GRN model;
      PurchaseOrder model; Invoice model; StockReservation model.
      Renames StockEntry.vendor (CharField) to vendor_legacy and adds vendor FK.
"""
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('ps1', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        # 1. Vendor model (T-07) — must be FIRST so the FK can reference it
        migrations.CreateModel(
            name='Vendor',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=250)),
                ('gst_number', models.CharField(blank=True, default='', max_length=15)),
                ('bank_account', models.CharField(blank=True, default='', max_length=30)),
                ('ifsc_code', models.CharField(blank=True, default='', max_length=11)),
                ('poc_name', models.CharField(blank=True, default='', max_length=250)),
                ('poc_email', models.EmailField(blank=True, default='')),
                ('poc_phone', models.CharField(blank=True, default='', max_length=15)),
                ('verification_status', models.CharField(
                    choices=[('Pending', 'Pending'), ('Verified', 'Verified'), ('Blocked', 'Blocked')],
                    default='Pending', max_length=20)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'db_table': 'Vendor'},
        ),

        # 2. ApprovalThreshold model (T-04)
        migrations.CreateModel(
            name='ApprovalThreshold',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('cost_limit', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('required_designation', models.CharField(max_length=250)),
                ('description', models.CharField(blank=True, max_length=500)),
            ],
            options={'db_table': 'ApprovalThreshold', 'ordering': ['cost_limit']},
        ),

        # 3. Add new status/flag fields to IndentFile (T-02, T-03, T-08, T-10, T-11)
        migrations.AddField(
            model_name='indentfile',
            name='status',
            field=models.CharField(
                choices=[
                    ('Draft', 'Draft'), ('Submitted', 'Submitted'), ('InReview', 'In Review'),
                    ('PendingClarification', 'Pending Clarification'),
                    ('ApprovedInternal', 'Approved – Internal Issue'),
                    ('ApprovedExternal', 'Approved – External Procurement'),
                    ('POIssued', 'PO Issued'), ('Dispatched', 'Dispatched'),
                    ('Received', 'Received'), ('InvoiceVerified', 'Invoice Verified'),
                    ('Paid', 'Paid'), ('Rejected', 'Rejected'), ('Cancelled', 'Cancelled'),
                    ('PendingWithdrawal', 'Pending Withdrawal'), ('Withdrawn', 'Withdrawn'),
                ],
                default='Submitted', max_length=30),
        ),
        migrations.AddField(
            model_name='indentfile',
            name='cancellation_reason',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='indentfile',
            name='rejection_reason',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='indentfile',
            name='is_duplicate_flag',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='indentfile',
            name='internal_issue_flag',
            field=models.BooleanField(default=False),
        ),

        # 4. Rename old vendor CharField → vendor_legacy on StockEntry (backward-compat)
        migrations.RenameField(
            model_name='stockentry',
            old_name='vendor',
            new_name='vendor_legacy',
        ),
        # 5. Relax the old vendor_legacy field (was required, now optional)
        migrations.AlterField(
            model_name='stockentry',
            name='vendor_legacy',
            field=models.CharField(
                blank=True, default='',
                help_text='Deprecated free-text vendor. Use vendor FK.',
                max_length=250),
        ),
        # 6. Add new vendor FK (references Vendor created in step 1)
        migrations.AddField(
            model_name='stockentry',
            name='vendor',
            field=models.ForeignKey(
                blank=True,
                help_text='Must be a verified Vendor record (BR-PS-012).',
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                to='ps1.vendor',
            ),
        ),

        # 7. GRN model (T-05)
        migrations.CreateModel(
            name='GRN',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('status', models.CharField(
                    choices=[
                        ('Pending', 'Pending Confirmation'), ('Confirmed', 'Confirmed – All OK'),
                        ('Discrepancy', 'Discrepancy Reported'), ('Rejected', 'Goods Rejected / Damaged'),
                    ], default='Pending', max_length=20)),
                ('confirmation_date', models.DateTimeField(blank=True, null=True)),
                ('discrepancy_note', models.TextField(blank=True, default='')),
                ('rejection_reason', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('indent_file', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='grn', to='ps1.indentfile')),
                ('confirmed_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    to='auth.user')),
            ],
            options={'db_table': 'GRN'},
        ),

        # 8. PurchaseOrder model (T-06)
        migrations.CreateModel(
            name='PurchaseOrder',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('po_number', models.CharField(max_length=50, unique=True)),
                ('total_amount', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('status', models.CharField(
                    choices=[('Draft', 'Draft'), ('Issued', 'Issued'), ('Received', 'Received'), ('Cancelled', 'Cancelled')],
                    default='Draft', max_length=20)),
                ('issued_date', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('indent_file', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='purchase_order', to='ps1.indentfile')),
                ('vendor', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.PROTECT, to='ps1.vendor')),
                ('issued_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL, to='auth.user')),
            ],
            options={'db_table': 'PurchaseOrder'},
        ),

        # 9. Invoice model (T-06)
        migrations.CreateModel(
            name='Invoice',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('invoice_number', models.CharField(max_length=50)),
                ('invoice_amount', models.DecimalField(decimal_places=2, max_digits=12)),
                ('status', models.CharField(
                    choices=[
                        ('Pending', 'Pending Verification'), ('Verified', 'Verified – 3-Way Match OK'),
                        ('OnHold', 'On Hold – Mismatch'), ('Rejected', 'Rejected'), ('Paid', 'Paid'),
                    ], default='Pending', max_length=20)),
                ('verification_date', models.DateTimeField(blank=True, null=True)),
                ('mismatch_reason', models.TextField(blank=True, default='')),
                ('invoice_file', models.FileField(blank=True, null=True, upload_to='')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('purchase_order', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='invoices', to='ps1.purchaseorder')),
                ('verified_by', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL, to='auth.user')),
            ],
            options={'db_table': 'Invoice'},
        ),

        # 10. StockReservation model (T-12)
        migrations.CreateModel(
            name='StockReservation',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('reserved_qty', models.IntegerField(default=1)),
                ('status', models.CharField(
                    choices=[
                        ('Active', 'Active'), ('Fulfilled', 'Fulfilled'),
                        ('Released', 'Released'), ('Expired', 'Expired'),
                    ], default='Active', max_length=20)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('expiry', models.DateTimeField(blank=True, null=True)),
                ('indent_file', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='reservations', to='ps1.indentfile')),
                ('stock_item', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='reservations', to='ps1.stockitem')),
            ],
            options={'db_table': 'StockReservation'},
        ),
    ]
