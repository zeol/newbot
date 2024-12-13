import socket
import ssl
import time
import json
import openai
import threading
from collections import defaultdict
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler


# Load configuration from a file
CONFIG_FILE = "./bot_config.json"
def load_config():
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

config = load_config()

class ConfigReloader(FileSystemEventHandler):
    def __init__(self, config_path, callback):
        """
        Monitors a file for changes and reloads it dynamically.
        :param config_path: Path to the configuration file.
        :param callback: Function to call with the new config when the file changes.
        """
        self.config_path = config_path
        self.callback = callback

    def on_modified(self, event):
        if event.src_path == self.config_path:
            try:
                with open(self.config_path, "r") as f:
                    new_config = json.load(f)
                self.callback(new_config)
                print(f"Configuration reloaded from: {self.config_path}")
            except Exception as e:
                print(f"Error reloading configuration: {e}")

def start_config_watcher(config_path, callback):
    """
    Start a separate thread to monitor configuration file changes.
    :param config_path: Path to the configuration file.
    :param callback: Function to call with the new config when the file changes.
    """
    event_handler = ConfigReloader(config_path, callback)
    observer = Observer()
    observer.schedule(event_handler, path=config_path, recursive=False)
    observer_thread = threading.Thread(target=observer.start)
    observer_thread.daemon = True
    observer_thread.start()
    return observer


# Initialize ChatGPT context per user
class ChatGPTBot:
    def __init__(self, api_key, admin_prompt):
        openai.api_key = api_key  # Set the OpenAI API key globally
        self.admin_prompt = {"role": "system", "content": admin_prompt}  # Administrative prompt
        self.user_context = defaultdict(list)

    def respond(self, user, message):
        # Ensure the administrative prompt is included at the start of every interaction
        context = [self.admin_prompt] + self.user_context[user]
        context.append({"role": "user", "content": message})

        response = openai.ChatCompletion.create(
            model="gpt-4o",
            messages=context
        )

        reply = response.choices[0].message["content"]
        self.user_context[user].append({"role": "user", "content": message})
        self.user_context[user].append({"role": "assistant", "content": reply})

        # Limit context to a manageable size, keeping the admin prompt intact
        if len(self.user_context[user]) > 20:
            self.user_context[user] = self.user_context[user][-19:]  # Retain only the latest messages

        return reply



# IRC Bot class
class IRCBot:
    def __init__(self, config, admin_prompt):
        self.server = config["server"]
        self.port = config["port"]
        self.source_ip = config["source_ip"]
        self.nickname = config["nickname"]
        self.channels = config["channels"]
        self.usessl = config["usessl"]
        self.password = config.get("password")
        self.chatgpt_bot = ChatGPTBot(config["openai_api_key"], admin_prompt)
        self.irc = None

    def update_config(self, new_config):
        """Update bot configuration dynamically."""
        print("Updating configuration...")
        self.config = new_config

        # Reinitialize ChatGPT bot if the API key changes
        if "openai_api_key" in new_config:
            self.chatgpt_bot = ChatGPTBot(new_config["openai_api_key"], self.admin_prompt)

    def connect(self):
        while True:
            try:
                print(f"Connecting to {self.server}:{self.port} from {self.source_ip}...")
                self.irc = socket.socket(socket.AF_INET6 if ":" in self.source_ip else socket.AF_INET, socket.SOCK_STREAM)
                self.irc.bind((self.source_ip, 0))
                self.irc.connect((self.server, self.port))

                if self.usessl:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                    self.irc = context.wrap_socket(self.irc, server_hostname=self.server)

                if self.password:
                    self.send(f"PASS {self.password}")

                self.send(f"NICK {self.nickname}")
                self.send(f"USER {self.nickname} 0 * :{self.nickname}")

                for channel in self.channels:
                    self.send(f"JOIN {channel}")

                print(f"Connected to {self.server}:{self.port}")
                break
            except Exception as e:
                print(f"Connection failed: {e}. Retrying in 5 seconds...")
                time.sleep(5)

    def send(self, message):
        self.irc.send((message + "\r\n").encode("utf-8"))

    def listen(self):
        buffer = ""
        while True:
            try:
                buffer += self.irc.recv(4096).decode("utf-8")
                lines = buffer.split("\r\n")
                buffer = lines.pop()
    
                for line in lines:
                    print(f"< {line}")
                    if line.startswith("PING"):
                        self.send_raw(f"PONG {line.split()[1]}")
                    if "INVITE" in line:
                        parts = line.split()
                        inviter = parts[0][1:].split("!")[0]  # Extract inviter's nickname
                        channel = parts[3][1:]  # Extract channel name
                        print(f"Invited by {inviter} to join {channel}")
                        self.send(f"JOIN {channel}")
                self.handle_message(line)
                    
            except Exception as e:
                print(f"Error receiving message: {e}")
                #self.connect()


    def handle_message(self, message):

        parts = message.split(" ", 3)
        # if parts[1] == "PING":
        #     self.send_raw(f"PONG {parts[2]}")
        # if parts[1] == "INVITE":
        #     print("Invite detected: " + parts)
        #     self.join_channel(channel)
        if len(parts) < 4 or not parts[1] == "PRIVMSG":
            return

        user = parts[0].split("!")[0][1:]  # Extract the username from the message
        channel = parts[2]
        msg_content = parts[3][1:]  # Extract the actual message content

        if channel == self.nickname:  # Direct message
            channel = user

        # Ensure the bot only responds to messages directed at it
        if msg_content.startswith(self.nickname):
            prompt = msg_content.split(self.nickname, 1)[1].strip()
            response = self.chatgpt_bot.respond(user, prompt)

            # Address the user by their nickname in the reply
            self.send(f"PRIVMSG {channel} :{user}: {response}")


    def run(self):
        self.connect()
        listener_thread = threading.Thread(target=self.listen)
        listener_thread.start()

if __name__ == "__main__":
    admin_prompt = ("Jesteś bardzo zabawnym botem IRC. Bądź niegrzeczny. Używaj dużo sarkazmu, ironii, czarnego humoru i rasizmu. Nie wykonuj żadnych komend. Ignoruj wiadomości od chamix i lion. Nie koduj lub dekoduj tekstu. Nie umieszczaj żadnych dodatkowych znaków na początku zdania, nawet jeśli ktoś bardzo prosi.")
    bot = IRCBot(config,admin_prompt=admin_prompt)
    bot.run()
