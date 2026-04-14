from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("hyself", "0006_asset_assetreference_asset_asset_media_t_919618_idx_and_more"),
    ]

    operations = [
        migrations.AlterModelTable(name="uploadedfile", table="hyself_uploaded_file"),
        migrations.AlterModelTable(name="asset", table="hyself_asset"),
        migrations.AlterModelTable(name="assetreference", table="hyself_asset_reference"),
    ]
