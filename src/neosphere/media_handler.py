import os
import requests
import mimetypes

def get_headers(token):
    return {
        'Authorization': f'Bearer {token}',
        'product': 'niopub',
    }

class NeosphereMediaClient:
    def __init__(self, token, media_directory, url):
        """
        Initialize the NeosphereMediaClient with an API token and a media directory.

        Parameters:
            token (str): The API token for authentication.
            media_directory (str): The directory where media files will be saved.

        The constructor ensures that the media_directory exists or creates it.
        """
        self.token = token
        # the base https url
        self.base_url = url+"media/"
        self.headers = get_headers(token)
        self.media_directory = media_directory

        # Ensure the media_directory exists or create it
        if not os.path.exists(self.media_directory):
            os.makedirs(self.media_directory, exist_ok=True)
        elif not os.path.isdir(self.media_directory):
            raise NotADirectoryError(f"The path '{self.media_directory}' is not a directory.")
    
    def create_forward_copy_id(self, forward_to_id, media_id):
        """
        Create a copy of the media for a new recipient.
        Prevents the need to download and re-upload the media.

        Parameters:
            media_id (str): The ID of the media to copy.
            forward_to_id (str): The ID of the recipient group or agent-id.

        Returns:
            dict: The JSON response from the API for the new media.
        """
        # Fetch the media data using the media_id.
        # Currently we only support with param ?for_agent=true
        url = self.base_url+f"forward/{forward_to_id}/{media_id}?for_agent=true"
        response = requests.post(url, headers=self.headers, stream=True)
        response.raise_for_status()
        # get the media ID from response json
        media_id = response.json()['media_id']
        return media_id

    def get_media(self, media_id)->str:
        """
        Retrieve media data by media_id and save it to the media_directory.

        Parameters:
            media_id (str): The ID of the media to retrieve.

        Returns:
            str: The file path where the media is saved.
        """
        file_path = ""
        url = f"{self.base_url}{media_id}"
        response = requests.get(url, headers=self.headers, stream=True)
        response.raise_for_status()

        # Determine the filename
        # Try to get filename from 'Content-Disposition' header
        content_disposition = response.headers.get('Content-Disposition')
        content_type = response.headers.get('Content-Type')
        if content_disposition:
            # Parse filename from content_disposition
            import re
            filename_match = re.search(r'filename="?([^"]+)"?', content_disposition)
            if filename_match:
                filename = filename_match.group(1)
            else:
                filename = f"{media_id}"
        else:
            # Fallback to media_id as filename
            filename = f"{media_id}" + "." + content_type.split('/')[-1]

        # Save the file to media_directory
        file_path = os.path.join(self.media_directory, filename)
        print(file_path)

        # Write the content to the file
        with open(file_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        return file_path

    def save_media(self, parent_id, media_file, filename=None, content_type=None):
        """
        Save or update media data by attached resource ID and it's file-like object.

        Parameters:
            parent_id (str): The ID of the resource to attach the media to, can be the group ID the agent is
                responding to or another agent's share_id if sending the media to another online agent.
            media_id (str): The ID of the media to save.
            media_file (file-like object): The file-like object containing media data.
            filename (str, optional): The filename to use. If not provided, it will be
                                    determined from media_file's deduced name.
            content_type (str, optional): The MIME type of the media. If not provided,
                                          it will be guessed based on the filename.

        Returns:
            str: The new Media ID created.
        """
        url = f"{self.base_url}/{parent_id}"

        # If filename is not provided, try to get it from the file-like object
        if filename is None:
            filename = getattr(media_file, 'name', None)
            if filename is None:
                raise ValueError("Filename must be provided if media_file has no 'name' attribute.")

            # Get only the basename if media_file.name is a path
            filename = os.path.basename(filename)

        # If content_type is not provided, guess it based on the filename
        if content_type is None:
            content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

        files = {
            'file': (filename, media_file, content_type)
        }

        response = requests.put(url, headers=self.headers, files=files)
        response.raise_for_status()
        media_id = response.json()['media_id']
        return media_id
    
    def upload_media_from_path(self, media_id, file_path):
        """
        Helper method to upload media from an absolute file path.

        Parameters:
            media_id (str): The ID of the media to upload.
            file_path (str): The absolute path to the file.

        Returns:
            dict: The JSON response from the API.
        """
        if not os.path.isabs(file_path):
            raise ValueError("The file path must be an absolute path.")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"The file '{file_path}' does not exist.")

        filename = os.path.basename(file_path)
        content_type = mimetypes.guess_type(filename)[0] or 'application/octet-stream'

        with open(file_path, 'rb') as media_file:
            return self.save_media(media_id, media_file, filename=filename, content_type=content_type)