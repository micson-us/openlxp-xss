import json
import logging
import os
import re
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from model_utils.models import TimeStampedModel

logger = logging.getLogger('dict_config_logger')


def validate_version(value):
    check = re.fullmatch('[0-9]*[.][0-9]*[.][0-9]*', value)
    if check is None:
        raise ValidationError(
            '%(value)s does not match the format 0.0.0',
            params={'value': value},
        )


class TermSet(TimeStampedModel):
    """Model for Termsets"""
    STATUS_CHOICES = [('published', 'published'),
                      ('retired', 'retired')]
    iri = models.SlugField(max_length=255, unique=True,
                           allow_unicode=True, primary_key=True)
    uuid = models.UUIDField(default=uuid4, editable=False, unique=True)
    name = models.SlugField(max_length=255, allow_unicode=True)
    version = models.CharField(max_length=255, validators=[validate_version])
    status = models.CharField(max_length=255, choices=STATUS_CHOICES)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    def save(self, *args, **kwargs):
        """Generate iri for item"""
        self.name = self.name.replace(' ', '_')
        self.iri = 'xss:' + self.version + '@' + self.name
        update_fields = kwargs.get('update_fields', None)
        if update_fields:
            kwargs['update_fields'] = set(update_fields).union({'iri'})

        super().save(*args, **kwargs)

    def export(self):
        children = {kid.name: kid.export()
                    for kid in self.children.filter(status='published')}
        terms = {term.name: term.export()
                 for term in self.terms.filter(status='published')}
        return {**children, **terms}

    def mapped_to(self, target_root):
        """Return dict of Terms mapped to anything in target_root string"""

        # filter out children with no mapped terms
        children = {kid.name: kid.mapped_to(target_root)
                    for kid in self.children.filter(status='published')}
        filtered_children = dict(
            filter(lambda kid: len(kid[1]) != 0, children.items()))

        # filter out terms that do not have a mapping
        terms = {term.name: term.mapped_to(target_root)
                 for term in self.terms.filter(status='published')}
        filtered_terms = dict(
            filter(lambda term: term[1] is not None, terms.items()))
        return {**filtered_children, **filtered_terms}


class ChildTermSet(TermSet):
    """Model for Child Termsets"""
    parent_term_set = models.ForeignKey(
        TermSet, on_delete=models.CASCADE, related_name='children')

    def save(self, *args, **kwargs):
        """Generate iri for item"""
        self.name = self.name.replace(' ', '_')
        self.iri = self.parent_term_set.iri + '/' + self.name
        self.version = self.parent_term_set.version
        update_fields = kwargs.get('update_fields', None)
        if update_fields:
            kwargs['update_fields'] = set(
                update_fields).union({'iri', 'version'})

        super(TermSet, self).save(*args, **kwargs)


class Term(TimeStampedModel):
    """Model for Terms"""
    STATUS_CHOICES = [('published', 'published'),
                      ('retired', 'retired')]
    USE_CHOICES = [('Required', 'Required'),
                   ('Optional', 'Optional'),
                   ('Recommended', 'Recommended'),
                   ]
    name = models.SlugField(max_length=255, allow_unicode=True)
    description = models.TextField(null=True, blank=True)
    iri = models.SlugField(max_length=255, unique=True,
                           allow_unicode=True, primary_key=True)
    uuid = models.UUIDField(default=uuid4, editable=False, unique=True)
    data_type = models.CharField(max_length=255, null=True, blank=True)
    use = models.CharField(max_length=255, choices=USE_CHOICES)
    source = models.CharField(max_length=255, null=True, blank=True)
    term_set = models.ForeignKey(
        TermSet, on_delete=models.CASCADE, related_name='terms')
    mapping = models.ManyToManyField('self', blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    status = models.CharField(max_length=255, choices=STATUS_CHOICES)

    def root_term_set(self):
        """Get iri of the root Term Set for the current Term"""
        if "/" in self.iri:
            return self.iri[:self.iri.index('/')]
        else:
            return self.iri[:self.iri.index('?')]

    def save(self, *args, **kwargs):
        """Generate iri for item"""
        self.name = self.name.replace(' ', '_')
        self.iri = self.term_set.iri + '?' + self.name
        update_fields = kwargs.get('update_fields', None)
        if update_fields:
            kwargs['update_fields'] = set(update_fields).union({'iri'})

        super().save(*args, **kwargs)

    def export(self):
        """convert key attributes of the Term to a dict"""
        attrs = {}
        attrs['use'] = self.use
        if(self.data_type is not None and self.data_type != ''):
            attrs['data_type'] = self.data_type
        if(self.source is not None and self.source != ''):
            attrs['source'] = self.source
        if(self.description is not None and self.description != ''):
            attrs['description'] = self.description
        return {**attrs}

    def path(self):
        """Get the path of the Term"""
        path = self.name
        ts = self.term_set

        # traverse the Term Sets to the root
        try:
            while ts.childtermset:
                path = ts.name + '.' + path
                ts = ts.childtermset.parent_term_set
        except ChildTermSet.DoesNotExist:
            return path

    def mapped_to(self, target_root):
        """Return path if Term is mapped to anything in target_root string"""
        target_map = self.mapping.filter(iri__startswith=target_root)
        if target_map.exists():
            return target_map.first().path()
        return None


class SchemaLedger(TimeStampedModel):
    """Model for Uploaded Schemas"""
    SCHEMA_STATUS_CHOICES = [('published', 'published'),
                             ('retired', 'retired')]

    schema_name = models.CharField(max_length=255)
    schema_iri = models.SlugField(max_length=255, unique=True,
                                  allow_unicode=True)
    uuid = models.UUIDField(default=uuid4, editable=False, unique=True)
    schema_file = models.FileField(upload_to='schemas/',
                                   null=True,
                                   blank=True)
    term_set = models.OneToOneField(
        TermSet, on_delete=models.CASCADE, related_name='schema', null=True,
        blank=True)
    status = models.CharField(max_length=255,
                              choices=SCHEMA_STATUS_CHOICES)
    metadata = models.JSONField(blank=True,
                                help_text="auto populated from uploaded file")
    version = models.CharField(max_length=255,
                               help_text="auto populated from other version "
                                         "fields")
    major_version = models.SmallIntegerField(default=0)
    minor_version = models.SmallIntegerField(default=0)
    patch_version = models.SmallIntegerField(default=0)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    class Meta:
        # can't save 2 schemas with the same name for the same version
        constraints = [
            models.UniqueConstraint(fields=['schema_name', 'version'],
                                    name='unique_schema')
        ]

    def filename(self):
        return os.path.basename(self.schema_file.name)

    def clean(self):
        # store the contents of the file in the metadata field
        if self.schema_file:
            json_file = self.schema_file
            json_obj = json.load(json_file)  # deserializes it

            self.metadata = json_obj
            json_file.close()
            self.schema_file = None

        # combine the versions
        version = \
            str(self.major_version) + '.' + str(self.minor_version) \
            + '.' + str(self.patch_version)
        self.version = version

    def __str__(self):
        return str(self.schema_iri)

    def save(self, *args, **kwargs):
        """Generate iri for item"""
        self.schema_iri = 'xss:' + self.version + '@' + self.schema_name
        update_fields = kwargs.get('update_fields', None)
        if update_fields:
            kwargs['update_fields'] = set(update_fields).union({'iri'})

        # super().save(*args, **kwargs)
        if self.pk is None:
            super(SchemaLedger, self).save(*args, **kwargs)
        else:
            super(SchemaLedger, self).save(update_fields=['status',
                                                          'updated_by'],
                                           *args, **kwargs)


class TransformationLedger(TimeStampedModel):
    """Model for Uploaded schema transformation mappings"""
    SCHEMA_STATUS_CHOICES = [('published', 'published'),
                             ('retired', 'retired')]

    source_schema = models.ForeignKey(TermSet,
                                      on_delete=models.CASCADE,
                                      related_name='source_mapping')
    target_schema = models.ForeignKey(TermSet,
                                      on_delete=models.CASCADE,
                                      related_name='target_mapping')
    uuid = models.UUIDField(default=uuid4, editable=False, unique=True)
    schema_mapping_file = models.FileField(upload_to='schemas/',
                                           null=True,
                                           blank=True)
    schema_mapping = \
        models.JSONField(blank=True,
                         help_text="auto populated from uploaded file")
    status = models.CharField(max_length=255,
                              choices=SCHEMA_STATUS_CHOICES)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)

    def clean(self):
        # store the contents of the file in the schema_mapping field
        if self.schema_mapping_file:
            json_file = self.schema_mapping_file
            json_obj = json.load(json_file)  # deserializes it

            self.schema_mapping = json_obj
            json_file.close()
            self.schema_mapping_file = None
