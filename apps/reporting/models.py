from django.conf import settings
from django.db import models


class Berichtsvariante(models.Model):
    """Gespeicherte Filtereinstellung eines Benutzers für einen Bericht (optional als Favorit im Dashboard)."""

    benutzer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="berichtsvarianten",
                                 verbose_name="Benutzer")
    bericht = models.CharField("Bericht (Code)", max_length=80)
    name = models.CharField("Name", max_length=100)
    filter = models.JSONField("Filter", default=dict, blank=True)
    favorit = models.BooleanField("Favorit im Dashboard", default=False)
    erstellt_am = models.DateTimeField("Erstellt am", auto_now_add=True)

    class Meta:
        verbose_name = "Berichtsvariante"
        verbose_name_plural = "Berichtsvarianten"
        ordering = ["bericht", "name"]
        constraints = [models.UniqueConstraint(fields=["benutzer", "bericht", "name"], name="variante_eindeutig")]

    def __str__(self):
        return f"{self.name} ({self.bericht})"

    def query(self):
        from urllib.parse import urlencode

        return urlencode(self.filter)
