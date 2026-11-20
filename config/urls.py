from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = "ERP Mittelstand – Administration"
admin.site.site_title = "ERP Admin"
admin.site.index_title = "Systemverwaltung"

urlpatterns = [
    path("anmelden/", auth_views.LoginView.as_view(), name="login"),
    path("abmelden/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("api/v1/", include("config.api_urls")),
    path("stammdaten/", include("apps.stammdaten.urls")),
    path("belege/", include("apps.belege.urls")),
    path("", include("apps.core.urls")),
]
