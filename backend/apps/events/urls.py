from django.urls import path

from . import views

urlpatterns = [
    path('event/create/', views.create_event, name ='create_event'), # POST — Permission check: Only the superuser, or staff can
    path('event/<slug:slug>/',  views.get_event, name='get_event'),  # GET
    path('event/<slug:slug>/detail/', views.event_detail_aggregate, name='event_detail_aggregate'),  # GET
    path('event/all',  views.getall_event, name='getall_event' ),  # GET
    path('event/all/user/<str:email>/',  views.getall_event_email, name='getall_event_email'),  # GET
    path('event/update/<slug:slug>/',  views.update_event, name='update_event'), # PUT|PATCH — Permission check: Only the event celebrant, superuser, or staff can
    path('event/delete/<slug:slug>/', views.delete_event, name='delete_event'),  # DELETE
    path('event/<slug:slug>/delete-impact/', views.get_event_delete_impact, name='get_event_delete_impact'),  # GET
    path('event/details-lock/', views.toggle_event_details_lock, name='toggle_event_details_lock'),  # PATCH

    path('event/<slug:event_slug>/event_day/create', views.create_eventday, name='create_event_day'),  # POST
    path('event/<slug:event_slug>/event_day/<uuid:id>', views.get_eventday_id, name='get_eventday_id'),  # GET
    path('event/event_day/all',  views.getall_eventday, name='getall_eventday'),  # GET
    path('event/user/event_day/<str:email>/',  views.getall_eventday_email, name='getall_eventday_email'),  # GET
    path('event/<slug:event_slug>/event_day/', views.getall_eventday_slug, name='getall_eventday_slug'),  # GET
    path('event/<slug:event_slug>/event_day/<uuid:id>/', views.update_eventday, name='update_eventday'),  # PUT|PATCH
    path('event/<slug:event_slug>/event_day/delete/<uuid:id>/', views.delete_eventday, name='delete_eventday'),  # DELETE
]
