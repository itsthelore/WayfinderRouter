#import <Foundation/Foundation.h>
#include <stdint.h>
#include <string.h>

@protocol WayfinderCredentialBrokerProtocol
- (void)resolveWithAccount:(NSString *)account
                 withReply:(void (^)(NSData *value, NSString *errorCode))reply;
@end

enum {
    WF_XPC_OK = 0,
    WF_XPC_MISSING = 1,
    WF_XPC_DENIED = 2,
    WF_XPC_TIMED_OUT = 3,
    WF_XPC_UNAVAILABLE = 4,
    WF_XPC_TOO_LARGE = 5,
};

int wayfinder_xpc_resolve(const char *account_bytes,
                          uint8_t *output,
                          size_t capacity,
                          size_t *output_length,
                          double timeout_seconds) {
    if (account_bytes == NULL || output == NULL || output_length == NULL ||
        capacity == 0 || !(timeout_seconds > 0.0)) {
        return WF_XPC_UNAVAILABLE;
    }
    *output_length = 0;
    NSString *account = [NSString stringWithUTF8String:account_bytes];
    if (account == nil) {
        return WF_XPC_UNAVAILABLE;
    }

    NSXPCConnection *connection =
        [[NSXPCConnection alloc] initWithServiceName:@"com.wayfinder.CredentialBroker"];
    connection.remoteObjectInterface =
        [NSXPCInterface interfaceWithProtocol:@protocol(WayfinderCredentialBrokerProtocol)];
    [connection resume];

    dispatch_semaphore_t completed = dispatch_semaphore_create(0);
    __block NSData *replyData = nil;
    __block NSString *replyError = nil;
    __block BOOL connectionFailed = NO;
    id<WayfinderCredentialBrokerProtocol> proxy =
        [connection remoteObjectProxyWithErrorHandler:^(NSError *error) {
            (void)error;
            connectionFailed = YES;
            dispatch_semaphore_signal(completed);
        }];
    [proxy resolveWithAccount:account withReply:^(NSData *value, NSString *errorCode) {
        replyData = value;
        replyError = errorCode;
        dispatch_semaphore_signal(completed);
    }];

    int64_t nanos = (int64_t)(timeout_seconds * (double)NSEC_PER_SEC);
    long wait_status = dispatch_semaphore_wait(
        completed, dispatch_time(DISPATCH_TIME_NOW, nanos));
    [connection invalidate];
    if (wait_status != 0) {
        return WF_XPC_TIMED_OUT;
    }
    if (connectionFailed) {
        return WF_XPC_UNAVAILABLE;
    }
    if (replyError != nil) {
        if ([replyError isEqualToString:@"missing"]) {
            return WF_XPC_MISSING;
        }
        if ([replyError isEqualToString:@"denied"] ||
            [replyError isEqualToString:@"invalid-reference"]) {
            return WF_XPC_DENIED;
        }
        return WF_XPC_UNAVAILABLE;
    }
    if (replyData.length > capacity) {
        return WF_XPC_TOO_LARGE;
    }
    if (replyData.length > 0) {
        memcpy(output, replyData.bytes, replyData.length);
    }
    *output_length = replyData.length;
    return WF_XPC_OK;
}
