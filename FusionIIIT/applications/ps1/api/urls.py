from django.conf.urls import url
from django.urls import path
from . import views

urlpatterns = [
    # ---------- Indent filing ----------
    url(r'^create_proposal/', views.createProposal, name='create-proposal'),
    url(r'^create_draft/', views.createDraft, name='create-draft'),
    url(r'^delete_indent/', views.delete_indent, name='delete-indents'),
    url(r'^view_indent/', views.getOneFiledIndent, name='view-indent'),
    path('forward_indent/<int:id>/', views.forwardIndent, name='create-indent'),
    path('user-suggestions', views.user_suggestions, name='user-suggestions'),
    url(r'^getDesignations/', views.getDesignations, name='get-designations'),

    # ---------- Indent views ----------
    path('indentview/<str:username>/', views.indentView, name='indent-view'),
    path('indentview2/<str:username>/', views.indentView2, name='indent-view2'),
    path('draftview/<str:username>/', views.draftView, name='draft-view'),
    url(r'^inwardIndents/(?P<id>\d+)$', views.inwardIndents, name='inward-indents'),
    url(r'^indentFile/(?P<id>\d+)$', views.indentFile, name='indent-file'),
    url(r'^indentFile/forward/(?P<id>\d+)$', views.ForwardIndentFile, name='forward-indent-file'),

    # ---------- Archive / Outbox ----------
    url(r'^archieve_indent/(?P<id>\d+)/$', views.archieve_file, name='archieve-file'),
    path('archieveview/<str:username>/', views.archieveview, name='archievedview'),
    path('outboxview2/<str:username>/', views.outboxview2, name='outboxview2'),

    # ---------- Stock ----------
    url(r'^entry/(?P<id>\d+)$', views.entry, name='entry'),
    url(r'^stock_entry_view/(?P<id>\d+)$', views.stockEntryView, name='stock-entry-view'),
    url(r'^current_stock_view/(?P<id>\d+)$', views.currentStockView, name='current-stock-view'),
    url(r'^stock_entry_item_view/(?P<id>\d+)$', views.stock_entry_item_view, name='stock-entry-item-view'),
    url(r'^stock_item_delete/(?P<id>\d+)$', views.stockDelete, name='stock-delete'),
    url(r'^stock_transfer/(?P<id>\d+)$', views.stockTransfer, name='stock-transfer'),
    url(r'^perform_transfer/(?P<id>\d+)$', views.performTransfer, name='perform-transfer'),
    path('stockEntry/<str:username>/', views.stockEntry, name='stock-entry'),

    # ---------- My Indents / Approve ----------
    path('my-indents/<str:username>/', views.my_indents_view, name='my-indents-view'),
    path('approve-indent/', views.approve_indent, name='approve_indent'),

    # ---------- T-02: Cancel intent (BR-PS-007, BR-PS-011) ----------
    url(r'^cancel-indent/', views.cancel_indent_view, name='cancel-indent'),

    # ---------- T-03: Reject indent (BR-PS-005) ----------
    url(r'^reject-indent/', views.reject_indent_view, name='reject-indent'),

    # ---------- T-05: Delivery confirmation (WF-002) ----------
    url(r'^confirm-delivery/', views.confirm_delivery_view, name='confirm-delivery'),
    url(r'^report-discrepancy/', views.report_discrepancy_view, name='report-discrepancy'),

    # ---------- T-06: Invoice verification (BR-PS-017, BR-PS-018) ----------
    url(r'^verify-invoice/(?P<invoice_id>\d+)$', views.verify_invoice_view, name='verify-invoice'),

    # ---------- T-07: Vendor management (BR-PS-012, UC-019) ----------
    url(r'^vendors/$', views.vendor_list_create, name='vendor-list-create'),
    url(r'^vendors/(?P<vendor_id>\d+)/verify/$', views.verify_vendor_view, name='verify-vendor'),
]