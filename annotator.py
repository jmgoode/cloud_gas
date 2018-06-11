import subprocess, uuid, os, json
from pathlib import Path
from shutil import copyfile
import boto3, botocore
from botocore.exceptions import ClientError
import time

#set paths --> assumes anntools is cloned to /home/ubuntu/cp-jmgoode/anntools on the instance.
#note: I use the anntools INSIDE of my git repo, not the one that comes on the instance
DIR_anntools = '/home/ubuntu/cp-jmgoode/anntools'
DIR_jobs = '/home/ubuntu/cp-jmgoode/anntools/jobs/' #jobs directory -- I use file system to persist data, as specified in HW

#set s3 and table resources
s3 = boto3.resource('s3', region_name='us-east-1')
dynamodb = boto3.resource('dynamodb', region_name='us-east-1')
ann_table = dynamodb.Table('jgoode_annotations')

#set SQS and queue resource
sqs = boto3.resource('sqs', region_name='us-east-1')
queue = sqs.Queue('https://sqs.us-east-1.amazonaws.com/127134666975/jgoode_job_requests')

# Poll the message queue in a loop
while True:
    # Attempt to read a message from the queue. Use long polling (waittime=20). This will return a Message object
    # Source: http://boto3.readthedocs.io/en/latest/reference/services/sqs.html#SQS.Queue.receive_messages
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

        #get current job status to make sure it's not already been completed
        # query = ann_table.get_item(Key={'job_id':job_id})
        # print(query)
        #status = myresponse['Item']['job_status']

        # check that filename ends with vcf and status is pending
        if filename.endswith('.vcf') and current_status == 'PENDING':

            #set download path (home/ubuntu/anntools/jobs/jobid/filename.vcf)
            download_path = DIR_jobs + job_id + '/' + filename
            print(download_path)
            #create a directory for the job
            job_folder_path = DIR_jobs + job_id #path to new job folder
            print(job_folder_path)

            try:
                os.mkdir(job_folder_path) #make directory with name job_id
            except:
                print('Folder path ' + job_folder_path + ' already exists. Deleting item from queue')

            #Get the input file S3 object and copy it to a local file
            try:
                # Download file from S3: http://boto3.readthedocs.io/en/latest/reference/services/s3.html#S3.Bucket.download_file
                s3.Bucket(bucket).download_file(key, download_path)
            except botocore.exceptions.ClientError as e:
                if e.response['Error']['Code'] == "404":
                    print("The object does not exist.")
                else:
                    raise

            #set path to run.py
            run_path = 'run.py'

            #file to be run by annotator
            input_path = 'jobs/' + job_id + '/' + filename

            #If job status is PENDING, update status to RUNNING, and spawn subprocess to run job
            try:
                data = {'job_id':job_id,
                        "user_id": user,
                        "name": name,
                        "email" : email,
                        "institution": institution,
                        "role": role,
                        "input_file_name": filename,
                        "s3_inputs_bucket": bucket,
                        "s3_key_input_file": key,
                        "submit_time": submit_time,
                        "job_status": 'RUNNING'}

                #Update status to running (checks to see status is pending first)
                #Source: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/GettingStarted.Python.03.html#GettingStarted.Python.03.03
                #Source(Conditional update): https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_UpdateItem.html#DDB-UpdateItem-request-ConditionExpression
                #Source(Conditional Update): https://stackoverflow.com/questions/49250374/aws-dynamodb-update-item-using-multiple-condition-expression
                ann_table.update_item(
                    Key={'job_id': job_id},
                    UpdateExpression="set job_status = :r",
                    ConditionExpression='job_status = :p',
                    ExpressionAttributeValues={':p':'PENDING',':r': 'RUNNING'}
                )

                #spawn subprocess and run job
                #source: https://docs.python.org/3/library/subprocess.html#subprocess.Popen
                x = subprocess.Popen(['python', run_path, input_path, job_id, user],cwd=DIR_anntools) #spawn subprocess, change cwd to anntools
                print(data)
                time.sleep(1)

                #delete the message from the queue
                #Sourec: http://boto3.readthedocs.io/en/latest/reference/services/sqs.html#SQS.Queue.delete
                sqs_message[0].delete()

                # Return response to notify user of successful submission
                response = {'code':200, 'data':data}
                print('Job ' + job_id + ' processed successfully!' + '\n\n' + str(response) + '\n\n')

            #Throw error for failed conditional updates
            except ClientError as e:
                if e.response['Error']['Code'] == "ConditionalCheckFailedException":
                    print(e.response['Error']['Message'])
                else:
                    raise

        #else filename does not end in .vcf OR job already processed
        else:
            if current_status != 'PENDING': #if status isnt pending, delete message.
                sqs_message[0].delete()
                print('Job ' + job_id + ' is already complete. Message removed from queue.')

            else:
                sqs_message[0].delete() #if status isnt ending in .vcf, delete message
                print('Error. Filename must end with .vcf. Message removed from queue')

    #if queue is empty, just keep running while loop until user manually terminates annotator program (Ctrl C)
    else:
        pass
