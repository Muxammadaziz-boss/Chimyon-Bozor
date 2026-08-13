from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='d_index'),
    path('create-category/', views.create_category, name='d_create_category'),
    path('list-category/', views.list_category, name='d_list_category'),
    path('edit-category/<int:id>/', views.edit_category, name='d_edit_category'),
    path('delete-category/<int:id>/', views.delete_category, name='d_delete_category'),
    path('create-product/', views.create_product, name='d_create_product'),
    path('list-product/', views.list_product, name='d_list_product'),
    path('edit-product/<str:code>/', views.edit_product, name='d_edit_product'),
    path('delete-product/<str:code>/', views.delete_product, name='d_delete_product'),
    path('list-orders/', views.orders, name='d_orders'),
    path('update-status/<str:code>/', views.status_update, name='d_update_status'),
    path('reject-cart/<str:code>/', views.reject_cart, name='d_reject_cart'),
    path('detail-orders/<str:code>/', views.cart_detail, name='d_detail_orders'),
    path('list-users/', views.list_users, name='d_list_users'),
    path('toggle-user/<int:id>/', views.toggle_user_status, name='d_toggle_user'),
    path('login/', views.log_in, name='d_login'),
    path('logout/', views.log_out, name='d_logout'),
    path('api/revenue-chart/', views.revenue_chart_data, name='revenue-chart'),
    path('export-orders/', views.export_orders, name='d_export_orders'),
]