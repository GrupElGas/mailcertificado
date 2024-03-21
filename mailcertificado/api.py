# -*- coding: utf-8 -*-

from suds.client import Client
from suds.xsd.doctor import ImportDoctor, Import
from suds import WebFault
import mimetypes
import re
import base64
from tempfile import TemporaryFile
from PyPDF2 import PdfFileMerger
from phonenumbers import parse, is_valid_number

class MailCertificadoException(Exception):

    def __init__(self, code, description):
        self.code = code
        self.description = description

    def __str__(self):
        return '(%s) %s' % (self.code,
                            self.description)


class MailCertificado(object):

    def __init__(self, user, password, test=False):
        """initialize

        :param user: mailcertificado connection user
        :type user: str or unicode
        :param password: mailcertificado connection password
        :type password: str or unicode
        :param test: use mailcertificado test api instead of production one
        :type test: boolean
        """

        self.user = user
        self.password = password
        if test:
            self.url = 'https://ws.codicert.tk/soap.php?wsdl'
            self.location = 'https://ws.codicert.tk/soap.php'
        else:
            self.url = 'https://ws.codicert.io/soap.php?wsdl'
            self.location = 'https://ws.codicert.io/soap.php'

    @property
    def credentials(self):
        """return credentials dict"""

        return {'user': self.user,
                'pass': self.password}

    @property
    def connection(self):
        """establish a connection with MailCertificado"""

        namespace = 'urn:MailcertificadoWS/types/'

        imp = Import('http://schemas.xmlsoap.org/soap/encoding/')
        imp.filter.add(namespace)
        doctor = ImportDoctor(imp)
        return Client(self.url, location=self.location, doctor=doctor)

    def valid_mobile(self, mobile):
        """checks if mobile number is valid"""

        mobile_re = re.compile('^[67][0-9]{8}$')

        return mobile_re.match(mobile[-9:]) and True or False

    def exception(self, exception):
        """raises and exception"""

        if isinstance(exception, str) and exception == 'unknown':
            raise MailCertificadoException(0, 'Unknown error')
        elif hasattr(exception, 'fault'):
            raise MailCertificadoException(exception.fault.faultactor,
                                           exception.fault.faultstring)
        else:
            raise MailCertificadoException(0, exception)

    def check_credit(self):
        """checks user's credit

        :returns: credit
        :rtype: float
        """

        connection = self.connection
        data = {'userData': self.credentials}
        try:
            res = connection.service.getUserCreditWS(data)
            res = res.credit
        except Exception as e:
            self.exception(e)
        return res

    def check_status(self, message_id):
        """checks the state of a message

        :param message_id: message identifier
        :type message_id: str or unicode
        :returns: dict(message_id: message identifier
                       transaction_id: request transaction
                       status: message status
                       date: date status was acquired)
        :rtype: dict
        """
        connection = self.connection
        data = {
            'userData': self.credentials,
            'messageId': message_id
        }
        try:
            res = connection.service.getMsgStatusWS(data)
            res = {'message_id': res.result.messageId,
                   'transaction_id': res.result.transactionId,
                   'status': res.result.status,
                   'date': res.result.date}
        except Exception as e:
            self.exception(e)
        return res

    def build_attachments(self, attachments, merge=False):
        """build attachments

        :param attachments: list of dict(name: attachment name
                                         data: base64 encoded content)
        :type attachments: list
        :param merge: merge all attachments in one file
        :returns: list of dict(name: attachment name
                               type: attachment mime type
                               size: attachment size in bits
                               data: base64 encoded content)
        :rtype: list
        """

        build = []
        if merge and len(attachments) > 1:
            # if merging all the attachments must be pdf's
            attach_types = [mimetypes.guess_type(x['name'])[0]
                            for x in attachments]
            if not (len(set(attach_types)) == 1
                    and attach_types[0] == 'application/pdf'):
                exception_message = 'Merge only allowed if all files are pdfs'
                self.exception(exception_message)
            merger = PdfFileMerger()
            merge_file = TemporaryFile()
            merge_name = attachments[0]['name']
            for attachment in attachments:
                #Write attachment data to a temporary file
                temp_file = TemporaryFile()
                temp_file.write(base64.b64decode(attachment['data']))
                temp_file.seek(0)
                merger.append(temp_file)
            merger.write(merge_file)
            merge_file.seek(0)
            merge_data = base64.b64encode(merge_file.read())
            attachments = [{'name': merge_name,
                            'data': merge_data}]
            merger.close()
            merge_file.close()

        for attachment in attachments:
            attach_name = attachment['name']
            attach_data = attachment['data']
            # Prepare attachment data
            # Guess the mime type using the extension of the file
            attach_type = mimetypes.guess_type(attach_name)
            if attach_type[0] is not None:
                attach_type = attach_type[0]
            else:
                exception_message = 'Cannot guess mime type for %s' % attach_name
                self.exception(exception_message)
            # Attach size will be estimated
            # from attach_data (base64 encoded)
            attach_size = int(len(attach_data) * 0.75)
            attach_dict = {
                'name': attach_name,
                'type': attach_type,
                'size': attach_size,
                'data': attach_data
            }
            build.append(attach_dict)
        return build

    def send_agreement(self, to, subject, body, attachments,
                       accept_method=None, accept_phone=None,
                       sms_phone=None, sms_body=None):
        """send agreement using mailcertificado

        :param to: recipient
        :type to: str or unicode
        :param subject: subject of message to send
        :type subject: str or unicode
        :param body: body of message to send
        :type body: str or unicode
        :param attachments: list of dict(name: attachment name
                                         data: base64 encoded content)
        :type attachments: list
        :param accept_method: use sms for sending signing code
        :type accept_method: int
        :param accept_phone: mobile number for sending signing code
        :type accept_phone: str or unicode
        :param sms_phone: notify email by sms
        :type sms_phone: str or unicode
        :param sms_body: body of sms for notifying
        :type sms_body: str or unicode
        :returns: messageId
        :rtype: str or unicode
        """
        # Do some previous checks prior to do anything
        # send acceptance code with sms
        if accept_method == 1:
            exception_message = ''
            # We need a phone number to send acceptance code
            if accept_phone is None:
                exception_message = (u"Cannot use sms acceptance "
                                     u"method without a phone number")
            # Check if the number passed is valid
            if not self.valid_mobile(accept_phone):
                exception_message = (u"Mobile phone %s does not seem "
                                     u"to be a valid number" % accept_phone)
            # if something encountered, raise
            if exception_message:
                self.exception(exception_message)
        # Check sms phone
        if sms_phone is not None and not self.valid_mobile(sms_phone):
            exception_message = (u"Mobile phone %s do not seem "
                                 u"to be a valid number" % sms_phone)
            self.exception(exception_message)

        connection = self.connection

        data = {
            'userData': self.credentials,
            'to': to,
            'subject': subject,
            'body': body,
            'attachments': self.build_attachments(attachments,
                                                  merge=True)
        }

        if sms_phone and sms_body:
            data.update({'smsPhone': sms_phone,
                         'smsBody': sms_body})

        if accept_method == 1:
            data.update({'acceptanceMethod': accept_method,
                         'acceptancePhone': accept_phone})

        try:
            res = connection.service.sendAgreementWS(data)
            if res is None:
                self.exception('unkown')
            res = res.result[0].messageId[0]
        except Exception as e:
            self.exception(e)
        return res

    def send_agreement_sms(self, to, subject, body, attachments):
        """send agreement using mailcertificado

        :param to: recipient
        :type to: str or unicode
        :param subject: subject of message to send
        :type subject: str or unicode
        :param body: body of message to send
        :type body: str or unicode
        :param attachments: list of dict(name: attachment name
                                         data: base64 encoded content)
        :returns: messageId
        :rtype: str or unicode
        """

        # Prefix aren't allowed in the sending. Delete prefix to can send the sms.
        if to:
            to = to[-9:]

        # Check to phone
        if to is not None and not self.valid_mobile(to):
            exception_message = (u"Mobile phone %s do not seem "
                                 u"to be a valid number" % to)
            self.exception(exception_message)

        connection = self.connection

        data = {
            'userData': self.credentials,
            'agreementSms': 1,
            'forwardOnError': 1,
            'to': to,
            'subject': subject,
            'body': body,
            'attachments': self.build_attachments(attachments,
                                                  merge=True)
        }

        try:
            res = connection.service.sendAgreementWS(data)
            if res is None:
                self.exception('unkown')
            res = res.result[0].messageId[0]
        except Exception as e:
            self.exception(e)
        return res

    def send_mail(self, to, subject, body, attachments,
                  sms_phone=None, sms_body=None):
        """send mail using mailcertificado

        :param to: recipient
        :type to: str or unicode
        :param subject: subject of message to send
        :type subject: str or unicode
        :param body: body of message to send
        :type body: str or unicode
        :param attachments: list of dict(name: attachment name
                                         data: base64 encoded content)
        :type attachments: list
        :param sms_phone: notify email by sms
        :type sms_phone: str or unicode
        :param sms_body: body of sms for notifying
        :type sms_body: str or unicode
        :returns: sended message identifier
        :rtype: str or unicode
        """
        # Do some previous checks prior to do anything
        # send acceptance code with sms

        # Check sms phone
        connection = self.connection

        data = {
            'userData': self.credentials,
            'to': to,
            'subject': subject,
            'body': body,
            'attachments': self.build_attachments(attachments, merge=True)
        }

        sms_phone = str(sms_phone)
        sms_phone = sms_phone.replace(' ', '')
        if (
                sms_phone and
                sms_body and
                len(sms_phone) > 8 and
                is_valid_number(parse(sms_phone, 'ES')) and
                self.valid_mobile(sms_phone)
        ):
            data.update({'smsPhone': sms_phone,
                         'smsBody': sms_body})

        try:
            res = connection.service.sendMailWS(data)
            if res is None:
                self.exception('unknown')
            res_id = ','.join([r.messageId[0] for r in res.result])
        except Exception as e:
            self.exception(e)
        return res_id

    def get_message(self, message_id):
        """gets stored eml in mailcertificado identified by message_id

        :param message_id: message identifier
        :type message_id: str or unicode
        :returns: dict(name: file name
                       data: base64 encoded content)
        :rtype: dict
        """

        connection = self.connection
        data = {'userData': self.credentials,
                'messageId': message_id}
        try:
            res = connection.service.getMsgWS(data)
            if res is None:
                self.exception('unkown')
            res = {'name': res.name,
                   'data': res.data}
        except Exception as e:
            self.exception(e)
        return res

    def get_message_certificate(self, message_id, cert_type='general'):
        """gets stored eml in mailcertificado identified by message_id

        :param message_id: message identifier
        :type message_id: str or unicode
        :param cert_type: type of certificate
                          (general, contract or mandate)
        :type cert_type: str or unicode
        :returns: dict(name: cert name
                       data: base64 encoded content)
        :rtype: dict
        """

        connection = self.connection
        data = {'userData': self.credentials,
                'messageId': message_id,
                'type': cert_type}
        try:
            res = connection.service.getMsgCertificateWS(data)
            if res is None:
                self.exception('unkown')
            res = {'name': res.name,
                   'data': res.data}
        except Exception as e:
            self.exception(e)
        return res

    def send_registered_sms(self, sms_phone, sms_body):
        """ Send a registered sms

        :param sms_phone: destination phone number
        :type sms_phone: str or unicode
        :param sms_body: body of sms
        :type sms_body: str or unicode
        :returns: sended message identifier
        :rtype: str or unicode
        """
        connection = self.connection

        data = {
            'userData': self.credentials,
            'smsPhone': sms_phone,
            'smsBody': sms_body,
        }

        try:
            res = connection.service.sendSmsWS(data)
            if res is None:
                self.exception('unknown')
            res = res.result[0].messageId[0]
        except Exception as e:
            self.exception(e)
        return res
