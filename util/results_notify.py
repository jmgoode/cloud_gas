import subprocess, uuid, os, json
from pathlib import Path
from shutil import copyfile
import boto3, botocore
from botocore.exceptions import ClientError

#set SQS and queue resource
sqs = boto3.resource('sqs', region_name='us-east-1')
queue = sqs.Queue('https://sqs.us-east-1.amazonaws.com/127134666975/jgoode_job_results')

#send email function
#source: Vas notes
#source: http://boto3.readthedocs.io/en/latest/reference/services/ses.html#SES.Client.send_email
def send_email_ses(recipients=None, sender=None, subject=None, body=None):
    ses = boto3.client('ses', region_name='us-east-1')
    response = ses.send_email(
    Destination = {'ToAddresses': recipients},
    Message={
      'Body': {'Text': {'Charset': "UTF-8", 'Data': body}},
      'Subject': {'Charset': "UTF-8", 'Data': subject},
    },
    Source=sender)
    return response['ResponseMetadata']['HTTPStatusCode']

# Poll the message queue in a loop
while True:
    # Attempt to read a message from the queue. Use long polling (waittime=20)
    #This extracts Message objects, returned in a list
    #source: http://boto3.readthedocs.io/en/latest/reference/services/sqs.html#SQS.Queue.receive_messages

    sqs_message = queue.receive_messages(
        MaxNumberOfMessages=1, #extract one message at a time (I intentionally throttle)
        WaitTimeSeconds=20, #polling time
    )

    # If message read (checks to see if item was extracted)
    if len(sqs_message) == 1:
        try:
            #extract job parameters from the message body as before
            raw_message = json.loads(sqs_message[0].body) #decoded json
            message = json.loads(raw_message['Message']) #decoded json

            #set variabales
            job_id = message['job_id']
            user = message['user_id']
            filename = message['input_file_name']
            name = message['name']
            email = message['email']
            institution = message['institution']
            role = message['role']
            bucket = message['s3_inputs_bucket']
            key = message['s3_key_input_file']
            submit_time = message['submit_time']
            current_status = message['job_status']
            s3_results_bucket = message['s3_results_bucket']
            ann_key = message['s3_key_result_file']
            log_key = message['s3_key_log_file']
            complete_time = message['complete_time']

            #set email variables and send email
            sender = 'jgoode@ucmpcs.org'
            recipients = []
            recipients.append(email)
            subject = 'AWS NOTIFICATION: Job ' + job_id + ' Completed.'
            url = 'https://jgoode.ucmpcs.org/annotations/' + job_id
            body = 'Dear ' + name + ', \n\n' + 'Job ' + job_id + ' has been completed successfully.\n\n' + \
            'Click to view: '+ url + '\n\n' + '~ The GAS Team '

            send_email_ses(recipients=recipients, sender=sender, subject=subject,body=body)

            #delete the message from the queue
            sqs_message[0].delete()

            #print 200 to stdout
            response = {'code':200, 'message':'Notification email sent to '+ email +' for job '+ job_id+ ' successfully'}
            print(response)

        #catch error and notify user
        except Exception as e:
            response = {'code':400, 'message':e}
            print(response)

    #if queue is empty, just keep running while loop until user manually terminates annotator program (Ctrl C)
    else:
        pass
