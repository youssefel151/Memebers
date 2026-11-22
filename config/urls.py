from django.contrib.auth import views as auth_views
from django.urls import path
from core import views
from core.admin_site import admin_site

urlpatterns = [
    path('admin/', admin_site.urls),
    path('', views.landing),
    path('login/', auth_views.LoginView.as_view(template_name='core/login.html')),
    path('password/reset/', auth_views.PasswordResetView.as_view(template_name='core/password_reset.html', email_template_name='core/password_reset_email.html', subject_template_name='core/password_reset_subject.txt', success_url='/password/reset/sent/'), name='password_reset'),
    path('password/reset/sent/', auth_views.PasswordResetDoneView.as_view(template_name='core/password_reset_done.html'), name='password_reset_done'),
    path('password/reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='core/password_reset_confirm.html', success_url='/password/reset/complete/'), name='password_reset_confirm'),
    path('password/reset/complete/', auth_views.PasswordResetCompleteView.as_view(template_name='core/password_reset_complete.html'), name='password_reset_complete'),
    path('logout/', auth_views.LogoutView.as_view()),
    path(
        'password/',
        auth_views.PasswordChangeView.as_view(
            template_name='core/password_change.html',
            success_url='/profile/',
        ),
    ),
    path('home/', views.home),
    path('profile/', views.profile),
    path('directory/', views.directory),
    path('messages/', views.inbox),
    path('messages/<int:user_id>/', views.conversation),
    path('members/', views.members_list, name='members'),
    path('members/<int:member_id>/remove/', views.member_remove, name='member_remove'),
    path('guests/', views.guests_list, name='guests'),
    path('events/', views.event_management, name='events'),
    path('events/new/', views.event_edit, name='event_new'),
    path('events/<int:event_id>/edit/', views.event_edit, name='event_edit'),
    path('events/<int:event_id>/delete/', views.event_delete, name='event_delete'),
    path('events/<int:event_id>/', views.event_detail, name='event_detail'),
    path('events/<int:event_id>/rsvp/', views.event_rsvp, name='event_rsvp'),
    path('events/<int:event_id>/report/', views.event_report, name='event_report'),
    path('announcements/', views.announcement_list, name='announcements'),
    path('announcements/new/', views.announcement_edit, name='announcement_new'),
    path('announcements/<int:announcement_id>/edit/', views.announcement_edit, name='announcement_edit'),
    path('announcements/<int:announcement_id>/delete/', views.announcement_delete, name='announcement_delete'),
    path('register/', views.register),
    path('invite/<uuid:token>/', views.invite),
    path('qr/<uuid:token>/', views.member_qr),
    path('guest-qr/<uuid:token>/', views.guest_qr),
    path('scan/', views.scan_page),
    path('scan/mark/', views.mark_attendance),
    path('members/add/', views.add_member),
]
