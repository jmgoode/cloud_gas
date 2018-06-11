import boto3, botocore
from botocore.exceptions import ClientError
from datetime import datetime
import time, os, json

# Created a new SQS queue (jgoode_archive_time-check) that subscribes to completed jobs topic

#Set resources
#set s3 resource

s3 = boto3.resource('s3', region_name='us-east-1')
#set SQS resource
sqs = boto3.resource('sqs', region_name='us-east-1')
queue = sqs.Queue('https://sqs.us-east-1.amazonaws.com/127134666975/jgoode_archive_time-check')

#set dynamo resource
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
ann_table = dynamodb.Table('jgoode_annotations')

#set glacier resource
glacier = boto3.client('glacier', region_name='us-east-1')

while True: #(continuous polling)
    # poll queue checking each message
    # http://boto3.readthedocs.io/en/latest/reference/services/sqs.html#SQS.Queue.receive_messages
    sqs_message = queue.receive_messages(
        MaxNumberOfMessages=1, #extract one message at a time
        WaitTimeSeconds=20, #polling time
    )
    # If message read (checks to see if item was extracted)
    if len(sqs_message) == 1:

        #extract job parameters from the message body as before
        raw_message = json.loads(sqs_message[0].body) #decoded json
        message = json.loads(raw_message['Message']) #decoded json

        job_id = message['job_id']
        #role = message['role']
        results_bucket = message['s3_results_bucket']
        ann_key = message['s3_key_result_file']
        complete_time = datetime.fromtimestamp(int(message['complete_time']))
        now = datetime.now()

        query = ann_table.get_item(Key={'job_id':job_id})
        role = query['Item']['role']

        #get time difference
        delta = now - complete_time

        #if the user role is premium
        #Should be up-to-date role b/c queried DB instead of reading from queue message (things could have changed)
        if role == 'premium_user':
            # Upload archveID=N/A and status=N/A (will use for displaying results)
            print('Premium User, no archive')
            #http://boto3.readthedocs.io/en/latest/reference/services/dynamodb.html#DynamoDB.Table.update_item
            ann_table.update_item(
                Key={
                    'job_id': job_id,
                },
                UpdateExpression="set results_file_archive_id = :a, archive_status = :b",
                ExpressionAttributeValues={
                    ':a': 'N/A',
                    ':b': 'N/A'
                }
            )

            #delete message from queue, we don't want to archive result to Glacier since user is premium
            sqs_message[0].delete()

        #Check to see if current time > completion time + 30 mins
        elif role =='free_user' and delta.seconds < 1800:
            pass

        #otherwise we must archive the file to glacier
        else:

            # archive to glacier

            # get contents from s3 object
            # http://boto3.readthedocs.io/en/latest/reference/services/s3.html#S3.Object.get
            obj = s3.Object(results_bucket, ann_key)
            result_file = obj.get()['Body']

            #Upload to glacier by passing contents to boto3 function
            #http://boto3.readthedocs.io/en/latest/reference/services/glacier.html#Glacier.Client.upload_archive
            response = glacier.upload_archive(vaultName='ucmpcs', archiveDescription=ann_key, body=result_file.read())
            archive_id = response['archiveId']
            print('ArchiveID: ' + archive_id)

            # persist Archive ID to database
            # http://boto3.readthedocs.io/en/latest/reference/services/dynamodb.html#DynamoDB.Table.update_item
            ann_table.update_item(
                Key={
                    'job_id': job_id,
                },
                UpdateExpression="set results_file_archive_id = :a, archive_status = :b",
                ExpressionAttributeValues={
                    ':a': archive_id,
                    ':b': 'ARCHIVED'
                } #archive ID
            )

            #delete file from s3
            #http://boto3.readthedocs.io/en/latest/reference/services/s3.html#S3.Object.delete
            object = s3.Object(results_bucket, ann_key)
            delete_response = object.delete()
            print('Deleted from S3: ' + str(delete_response))

            #delete message from queue, since we only archive once
            sqs_message[0].delete()

    #if queue is empty, keep running while loop
    else:
        pass
