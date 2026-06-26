from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('emf', '0003_kioskorderref'),
    ]

    operations = [
        migrations.DeleteModel(
            name='KioskOrderRef',
        ),
    ]
