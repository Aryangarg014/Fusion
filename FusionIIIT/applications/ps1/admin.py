from django.contrib import admin
from applications.ps1.models import (
    StockEntry, IndentFile, StockItem, StockTransfer,
    Vendor, GRN, PurchaseOrder, Invoice, StockReservation,
    ApprovalThreshold,
)

admin.site.register(StockEntry)
admin.site.register(IndentFile)
admin.site.register(StockItem)
admin.site.register(StockTransfer)
admin.site.register(Vendor)
admin.site.register(GRN)
admin.site.register(PurchaseOrder)
admin.site.register(Invoice)
admin.site.register(StockReservation)
admin.site.register(ApprovalThreshold)