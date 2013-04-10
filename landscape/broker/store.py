"""Message storage.

The sequencing system we use in the message store may be quite confusing
if you haven't looked at it in the last 10 minutes. For that reason, let's
review the mechanics here.

Our goal is to implement a reasonably robust system for delivering messages
from us to our peer. The system should be smart enough to recover if the peer
happens to lose messages that we have already sent, provided that these
messages are not too old (we'll see below what 'too old' means).

Messages added to the store are identified by increasing natural numbers, the
first message added is identified by 0, the second by 1, and so on. We call
"sequence" the number identifying the next message that we want to send. For
example, if the store has been added ten messages (that we represent with
uppercase letters) and we want start sending the first of them, our store
would like like::

    sequence: 0
    messages: A, B, C, D, E, F, G, H, I, J
              ^

The "^" marker is what we call "pending offset" and is the displacement of the
message we want to send next from the first message we have in the store.

Let's say we now send to our peer a batch of 3 sequential messages. In the
payload we include the body of the messages being sent and the sequence, which
identifies the first message of the batch. In this case the payload would look
like (pseudo-code)::

    (sequence: 0, messages: A, B, C)

If everything works fine on the other end, our peer replies with a payload that
would like::

    (next-expected-sequence: 4)

meaning that the peer as received all the three messages that we sent, an so
the next message it expects to receive is the one identified by the number 4.
At this point we update both our pending offset and our sequence values, and
the store now looks like::

    sequence: 4
    messages: A, B, C, D, E, F, G, H, I, J
                       ^

Great, now let's pretend that we send another batch, this time with five
messages::

    (sequence: 4, messages: D, E, F, G, H)

Our peer receives them fine responding with a payload looking like::

    (next-expected-sequence: 9)

meaning that it received all the eight messages we sent so far and it's waiting
for the ninth. This is the second successful batch that we send in a row, so we
can be reasonably confident that at least the messages in the first batch are
not really needed anymore. We delete them and we update our sequence and
pending offset accordingly::

    sequence: 9
    messages: D, E, F, G, H, I, J
                             ^

Note that we still want to keep around the messages we sent in the very last
batch, just in case. Indeed we now try to send a third batch with the last two
messages that we have, but our peer surprisingly replies us with this payload::

    (next-expected-sequence: 6)

Ouch! This means that something bad happened and our peer has somehow lost not
only the two messages that we sent in the last batch, but also the last three
messages of the former batch :(

Luckly we've kept enough old messages around that we can try to send them
again, we update our sequence and pending offset and the store looks like::

    sequence: 6
    messages: D, E, F, G, H, I, J
                    ^

We can now start again sending messages using the same strategy.

Note however that in the worst case scenario we could receive from our peer
a next-expected-sequence value which is so old to be outside our buffer
of already-sent messages. In that case there is now way we can recover the
lost messages, and we'll just send the oldest one that we have.

See L{MessageStore} for details about how messages are stored on the file
system and L{landscape.lib.message.got_next_expected} to check how the
strategy for updating the pending offset and the sequence is implemented.
"""

import time
import itertools
import logging
import os

from landscape.lib import bpickle
from landscape import SERVER_API


HELD = "h"
BROKEN = "b"


class MessageStore(object):
    """A message store which stores its messages in a file system hierarchy.

    Beside the "sequence" and the "pending offset" values described in the
    module docstring above, the L{MessageStore} also stores what we call
    "server sequence", which is the next message number expected by the
    *client* itself (because we are in turn the peer of a specular message
    system running in the server, which tries to deliver messages to us).

    The server sequence is entirely unrelated to the stored messages, but is
    incremented when successfully receiving messages from the server, in the
    very same way described above but with the roles inverted.

    @param persist: a L{Persist} used to save state parameters like the
        accepted message types, sequence, server uuid etc.
    @param directory: base of the file system hierarchy
    """

    api = SERVER_API

    def __init__(self, persist, directory, directory_size=1000,
                 get_time=time.time):
        self._get_time = get_time
        self._directory = directory
        self._directory_size = directory_size
        self._schemas = {}
        self._original_persist = persist
        self._persist = persist.root_at("message-store")
        message_dir = self._message_dir()
        if not os.path.isdir(message_dir):
            os.makedirs(message_dir)

    def commit(self):
        """Persist metadata to disk."""
        self._original_persist.save()

    def set_accepted_types(self, types):
        """Specify the types of messages that the server will expect from us.

        If messages are added to the store which are not currently
        accepted, they will be saved but ignored until their type is
        accepted.
        """
        assert type(types) in (tuple, list, set)
        self._persist.set("accepted-types", sorted(set(types)))
        self._reprocess_holding()

    def get_accepted_types(self):
        """Get a list of all accepted message types."""
        return self._persist.get("accepted-types", ())

    def accepts(self, type):
        """Return bool indicating if C{type} is an accepted message type."""
        return type in self.get_accepted_types()

    def get_sequence(self):
        """Get the current sequence.

        @return: The sequence number of the message that the server expects us
            to send on the next exchange.
        """
        return self._persist.get("sequence", 0)

    def set_sequence(self, number):
        """Set the current sequence.

        Set the sequence number of the message that the server expects us to
        send on the next exchange.
        """
        self._persist.set("sequence", number)

    def get_server_sequence(self):
        """Get the current server sequence.

        @return: the sequence number of the message that we will ask the server
            to send to us on the next exchange.
        """
        return self._persist.get("server_sequence", 0)

    def set_server_sequence(self, number):
        """Set the current server sequence.

        Set the sequence number of the message that we will ask the server to
        send to us on the next exchange.
        """
        self._persist.set("server_sequence", number)

    def get_server_uuid(self):
        """Return the currently set server UUID."""
        return self._persist.get("server_uuid")

    def set_server_uuid(self, uuid):
        """Change the known UUID from the server we're communicating to."""
        self._persist.set("server_uuid", uuid)

    def get_exchange_token(self):
        """Get the authentication token to use for the next exchange."""
        return self._persist.get("exchange_token")

    def set_exchange_token(self, token):
        """Set the authentication token to use for the next exchange."""
        self._persist.set("exchange_token", token)

    def get_pending_offset(self):
        """Get the current pending offset."""
        return self._persist.get("pending_offset", 0)

    def set_pending_offset(self, val):
        """Set the current pending offset.

        Set the offset into the message pool to consider assigned to the
        current sequence number as returned by l{get_sequence}.
        """
        self._persist.set("pending_offset", val)

    def add_pending_offset(self, val):
        """Increment the current pending offset by C{val}."""
        self.set_pending_offset(self.get_pending_offset() + val)

    def count_pending_messages(self):
        """Return the number of pending messages."""
        return sum(1 for x in self._walk_pending_messages())

    def get_pending_messages(self, max=None):
        """Get any pending messages that aren't being held, up to max."""
        accepted_types = self.get_accepted_types()
        messages = []
        for filename in self._walk_pending_messages():
            if max is not None and len(messages) >= max:
                break
            data = self._get_content(self._message_dir(filename))
            try:
                message = bpickle.loads(data)
            except ValueError, e:
                logging.exception(e)
                self._add_flags(filename, BROKEN)
            else:
                if message["type"] not in accepted_types:
                    self._add_flags(filename, HELD)
                else:
                    messages.append(message)
        return messages

    def delete_old_messages(self):
        """Delete messages which are unlikely to be needed in the future."""
        for fn in itertools.islice(self._walk_messages(exclude=HELD + BROKEN),
                                   self.get_pending_offset()):
            os.unlink(fn)
            containing_dir = os.path.split(fn)[0]
            if not os.listdir(containing_dir):
                os.rmdir(containing_dir)

    def delete_all_messages(self):
        """Remove ALL stored messages."""
        self.set_pending_offset(0)
        for filename in self._walk_messages():
            os.unlink(filename)

    def add_schema(self, schema):
        """Add a schema to be applied to messages of the given type.

        The schema must be an instance of L{landscape.schema.Message}.
        """
        self._schemas[schema.type] = schema

    def is_pending(self, message_id):
        """Return bool indicating if C{message_id} still hasn't been delivered.

        @param message_id: Identifier returned by the L{add()} method.
        """
        i = 0
        pending_offset = self.get_pending_offset()
        for filename in self._walk_messages(exclude=BROKEN):
            flags = self._get_flags(filename)
            if ((HELD in flags or i >= pending_offset) and
                os.stat(filename).st_ino == message_id):
                return True
            if BROKEN not in flags and HELD not in flags:
                i += 1
        return False

    def add(self, message):
        """Queue a message for delivery.

        @param message: a C{dict} with a C{type} key and other keys conforming
            to the L{Message} schema for that specifc message type.
        @return: message_id, which is an identifier for the added message.
        """
        assert "type" in message
        message = self._schemas[message["type"]].coerce(message)

        if "api" not in message:
            message["api"] = self.api

        message_data = bpickle.dumps(message)

        filename = self._get_next_message_filename()

        file = open(filename + ".tmp", "w")
        file.write(message_data)
        file.close()
        os.rename(filename + ".tmp", filename)

        if not self.accepts(message["type"]):
            filename = self._set_flags(filename, HELD)

        # For now we use the inode as the message id, as it will work
        # correctly even faced with holding/unholding.  It will break
        # if the store is copied over for some reason, but this shouldn't
        # present an issue given the current uses.  In the future we
        # should have a nice transactional storage (e.g. sqlite) which
        # will offer a more strong primary key.
        message_id = os.stat(filename).st_ino

        return message_id

    def _get_next_message_filename(self):
        message_dirs = self._get_sorted_filenames()
        if message_dirs:
            newest_dir = message_dirs[-1]
        else:
            os.makedirs(self._message_dir("0"))
            newest_dir = "0"

        message_filenames = self._get_sorted_filenames(newest_dir)
        if not message_filenames:
            filename = self._message_dir(newest_dir, "0")
        elif len(message_filenames) < self._directory_size:
            filename = str(int(message_filenames[-1].split("_")[0]) + 1)
            filename = self._message_dir(newest_dir, filename)
        else:
            newest_dir = self._message_dir(str(int(newest_dir) + 1))
            os.makedirs(newest_dir)
            filename = os.path.join(newest_dir, "0")

        return filename

    def _walk_pending_messages(self):
        """Walk the files which are definitely pending."""
        pending_offset = self.get_pending_offset()
        for i, filename in enumerate(self._walk_messages(exclude=HELD +
                                                                 BROKEN)):
            if i >= pending_offset:
                yield filename

    def _walk_messages(self, exclude=None):
        if exclude:
            exclude = set(exclude)
        message_dirs = self._get_sorted_filenames()
        for message_dir in message_dirs:
            for filename in self._get_sorted_filenames(message_dir):
                flags = set(self._get_flags(filename))
                if (not exclude or not exclude & flags):
                    yield self._message_dir(message_dir, filename)

    def _get_sorted_filenames(self, dir=""):
        message_files = [x for x in os.listdir(self._message_dir(dir))
                         if not x.endswith(".tmp")]
        message_files.sort(key=lambda x: int(x.split("_")[0]))
        return message_files

    def _message_dir(self, *args):
        return os.path.join(self._directory, *args)

    def _get_content(self, filename):
        file = open(filename)
        try:
            return file.read()
        finally:
            file.close()

    def _reprocess_holding(self):
        """
        Unhold accepted messages left behind, and hold unaccepted
        pending messages.
        """
        offset = 0
        pending_offset = self.get_pending_offset()
        accepted_types = self.get_accepted_types()
        for old_filename in self._walk_messages():
            flags = self._get_flags(old_filename)
            try:
                message = bpickle.loads(self._get_content(old_filename))
            except ValueError, e:
                logging.exception(e)
                if HELD not in flags:
                    offset += 1
            else:
                accepted = message["type"] in accepted_types
                if HELD in flags:
                    if accepted:
                        new_filename = self._get_next_message_filename()
                        os.rename(old_filename, new_filename)
                        self._set_flags(new_filename, set(flags) - set(HELD))
                else:
                    if not accepted and offset >= pending_offset:
                        self._set_flags(old_filename, set(flags) | set(HELD))
                    offset += 1

    def _get_flags(self, path):
        basename = os.path.basename(path)
        if "_" in basename:
            return basename.split("_")[1]
        return ""

    def _set_flags(self, path, flags):
        dirname, basename = os.path.split(path)
        new_path = os.path.join(dirname, basename.split("_")[0])
        if flags:
            new_path += "_" + "".join(sorted(set(flags)))
        os.rename(path, new_path)
        return new_path

    def _add_flags(self, path, flags):
        self._set_flags(path, self._get_flags(path) + flags)


def get_default_message_store(*args, **kwargs):
    """
    Get a L{MessageStore} object with all Landscape message schemas added.
    """
    from landscape. message_schemas import message_schemas
    store = MessageStore(*args, **kwargs)
    for schema in message_schemas.values():
        store.add_schema(schema)
    return store
