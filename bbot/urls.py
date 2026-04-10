from django.urls import path

from . import views

app_name = 'bbot'

urlpatterns = [
    path('index/', views.index, name='index'),
    path("upload/files/", views.FileEntriesAPIView.as_view(), name="upload_files"),
    path("upload/search/", views.SearchFileEntriesAPIView.as_view(), name="upload_search"),
    path("upload/recycle-bin/", views.RecycleBinEntriesAPIView.as_view(), name="upload_recycle_bin"),
    path("upload/recycle-bin/restore/", views.RestoreRecycleBinEntryAPIView.as_view(), name="upload_recycle_bin_restore"),
    path("upload/recycle-bin/clear/", views.ClearRecycleBinAPIView.as_view(), name="upload_recycle_bin_clear"),
    path("upload/folders/", views.CreateFolderAPIView.as_view(), name="upload_folders"),
    path("upload/chat-attachments/save/", views.SaveChatAttachmentToResourceAPIView.as_view(), name="upload_chat_attachment_save"),
    path("upload/delete/", views.DeleteFileEntryAPIView.as_view(), name="upload_delete"),
    path("upload/rename/", views.RenameFileEntryAPIView.as_view(), name="upload_rename"),
    path("upload/small/", views.UploadSmallFileAPIView.as_view(), name="upload_small"),
    path("upload/precheck/", views.UploadPrecheckAPIView.as_view(), name="upload_precheck"),
    path("upload/chunks/", views.UploadedChunksAPIView.as_view(), name="upload_chunks"),
    path("upload/chunk/", views.UploadChunkAPIView.as_view(), name="upload_chunk"),
    path("upload/merge/", views.UploadMergeAPIView.as_view(), name="upload_merge"),
]