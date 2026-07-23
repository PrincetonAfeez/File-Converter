# "Root URL configuration."
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import TemplateView

from apps.organizations.auth import ThrottledLoginView

urlpatterns = [
    path("admin/", admin.site.urls),
    path(
        "accounts/login/",
        ThrottledLoginView.as_view(template_name="registration/login.html"),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("legal/terms/", TemplateView.as_view(template_name="legal/terms.html"), name="terms"),
    path(
        "legal/privacy/",
        TemplateView.as_view(template_name="legal/privacy.html"),
        name="privacy",
    ),
    path("ops/", include("apps.ops.urls")),
    path("", include("apps.conversions.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
