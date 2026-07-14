#import <Foundation/Foundation.h>
#include <stdint.h>
#include <string.h>

@protocol WayfinderFoundationModelStreamSink
- (void)receive:(NSData *)event;
- (void)finish:(NSString *)errorCode;
@end

@protocol WayfinderFoundationModelBrokerProtocol
- (void)availability:(NSData *)request withReply:(void (^)(NSData *, NSString *))reply;
- (void)generate:(NSData *)request withReply:(void (^)(NSData *, NSString *))reply;
- (void)stream:(NSData *)request to:(id<WayfinderFoundationModelStreamSink>)sink;
- (void)cancel:(NSString *)requestID withReply:(void (^)(NSString *))reply;
@end

typedef int (*wayfinder_foundation_event_callback)(void *, const uint8_t *, size_t);

enum {
    WF_FOUNDATION_OK = 0,
    WF_FOUNDATION_TIMED_OUT = 1,
    WF_FOUNDATION_UNAVAILABLE = 2,
    WF_FOUNDATION_DENIED = 3,
    WF_FOUNDATION_REQUEST_TOO_LARGE = 4,
    WF_FOUNDATION_RESPONSE_TOO_LARGE = 5,
    WF_FOUNDATION_UNSUPPORTED_VERSION = 6,
    WF_FOUNDATION_CANCELLED = 7,
    WF_FOUNDATION_GENERATION_FAILED = 8,
};

static const size_t WF_MAX_REQUEST_BYTES = 1048576;

static int status_for_error(NSString *errorCode) {
    if (errorCode == nil) return WF_FOUNDATION_OK;
    if ([errorCode isEqualToString:@"timed-out"] || [errorCode isEqualToString:@"invalid-timeout"]) return WF_FOUNDATION_TIMED_OUT;
    if ([errorCode isEqualToString:@"unauthorized"]) return WF_FOUNDATION_DENIED;
    if ([errorCode isEqualToString:@"request-too-large"] || [errorCode isEqualToString:@"invalid-content"] || [errorCode isEqualToString:@"invalid-request-id"]) return WF_FOUNDATION_REQUEST_TOO_LARGE;
    if ([errorCode isEqualToString:@"bound-exceeded"]) return WF_FOUNDATION_RESPONSE_TOO_LARGE;
    if ([errorCode isEqualToString:@"unsupported-version"]) return WF_FOUNDATION_UNSUPPORTED_VERSION;
    if ([errorCode isEqualToString:@"cancelled"]) return WF_FOUNDATION_CANCELLED;
    if ([errorCode isEqualToString:@"generation-failed"] || [errorCode isEqualToString:@"malformed-payload"]) return WF_FOUNDATION_GENERATION_FAILED;
    return WF_FOUNDATION_UNAVAILABLE;
}

@interface WayfinderFoundationConnectionState : NSObject
@property(nonatomic, strong) dispatch_semaphore_t completed;
@property(nonatomic, assign) BOOL failed;
@end
@implementation WayfinderFoundationConnectionState
@end

static id<WayfinderFoundationModelBrokerProtocol> proxy_for_connection(
    NSXPCConnection *connection, WayfinderFoundationConnectionState *state) {
    NSXPCInterface *brokerInterface =
        [NSXPCInterface interfaceWithProtocol:@protocol(WayfinderFoundationModelBrokerProtocol)];
    NSXPCInterface *streamInterface =
        [NSXPCInterface interfaceWithProtocol:@protocol(WayfinderFoundationModelStreamSink)];
    [brokerInterface setInterface:streamInterface
                      forSelector:@selector(stream:to:)
                    argumentIndex:1
                          ofReply:NO];
    connection.remoteObjectInterface = brokerInterface;
    [connection resume];
    return [connection remoteObjectProxyWithErrorHandler:^(NSError *error) {
        (void)error;
        state.failed = YES;
        dispatch_semaphore_signal(state.completed);
    }];
}

int wayfinder_foundation_xpc_request(int operation,
                                     const uint8_t *request,
                                     size_t requestLength,
                                     uint8_t *output,
                                     size_t outputCapacity,
                                     size_t *outputLength,
                                     double timeoutSeconds) {
    if (request == NULL || output == NULL || outputLength == NULL ||
        requestLength == 0 || requestLength > WF_MAX_REQUEST_BYTES ||
        outputCapacity == 0 || !(timeoutSeconds > 0.0) ||
        (operation != 0 && operation != 1)) {
        return WF_FOUNDATION_REQUEST_TOO_LARGE;
    }
    *outputLength = 0;
    NSData *requestData = [NSData dataWithBytes:request length:requestLength];
    NSXPCConnection *connection = [[NSXPCConnection alloc]
        initWithServiceName:@"com.wayfinder.FoundationModelBroker"];
    dispatch_semaphore_t completed = dispatch_semaphore_create(0);
    WayfinderFoundationConnectionState *state = [WayfinderFoundationConnectionState new];
    state.completed = completed;
    __block NSData *replyData = nil;
    __block NSString *replyError = nil;
    id<WayfinderFoundationModelBrokerProtocol> proxy =
        proxy_for_connection(connection, state);
    void (^reply)(NSData *, NSString *) = ^(NSData *data, NSString *errorCode) {
        replyData = data;
        replyError = errorCode;
        dispatch_semaphore_signal(completed);
    };
    if (operation == 0) [proxy availability:requestData withReply:reply];
    else [proxy generate:requestData withReply:reply];

    int64_t nanos = (int64_t)(timeoutSeconds * (double)NSEC_PER_SEC);
    long waitStatus = dispatch_semaphore_wait(completed, dispatch_time(DISPATCH_TIME_NOW, nanos));
    [connection invalidate];
    if (waitStatus != 0) return WF_FOUNDATION_TIMED_OUT;
    if (state.failed) return WF_FOUNDATION_UNAVAILABLE;
    int status = status_for_error(replyError);
    if (status != WF_FOUNDATION_OK) return status;
    if (replyData == nil) return WF_FOUNDATION_UNAVAILABLE;
    if (replyData.length > outputCapacity) return WF_FOUNDATION_RESPONSE_TOO_LARGE;
    memcpy(output, replyData.bytes, replyData.length);
    *outputLength = replyData.length;
    return WF_FOUNDATION_OK;
}

@interface WayfinderFoundationStreamReceiver : NSObject <WayfinderFoundationModelStreamSink>
@property(nonatomic, assign) wayfinder_foundation_event_callback callback;
@property(nonatomic, assign) void *context;
@property(nonatomic, strong) dispatch_semaphore_t completed;
@property(nonatomic, copy) NSString *errorCode;
@property(nonatomic, assign) BOOL stopped;
@end

@implementation WayfinderFoundationStreamReceiver
- (void)receive:(NSData *)event {
    if (self.stopped) return;
    if (event.length > WF_MAX_REQUEST_BYTES || self.callback(self.context, event.bytes, event.length) != 0) {
        self.errorCode = @"bound-exceeded";
        self.stopped = YES;
        dispatch_semaphore_signal(self.completed);
    }
}
- (void)finish:(NSString *)errorCode {
    if (self.stopped) return;
    self.errorCode = errorCode;
    self.stopped = YES;
    dispatch_semaphore_signal(self.completed);
}
@end

int wayfinder_foundation_xpc_stream(const uint8_t *request,
                                    size_t requestLength,
                                    wayfinder_foundation_event_callback callback,
                                    void *context,
                                    double timeoutSeconds) {
    if (request == NULL || callback == NULL || context == NULL || requestLength == 0 ||
        requestLength > WF_MAX_REQUEST_BYTES || !(timeoutSeconds > 0.0)) {
        return WF_FOUNDATION_REQUEST_TOO_LARGE;
    }
    NSData *requestData = [NSData dataWithBytes:request length:requestLength];
    NSXPCConnection *connection = [[NSXPCConnection alloc]
        initWithServiceName:@"com.wayfinder.FoundationModelBroker"];
    dispatch_semaphore_t completed = dispatch_semaphore_create(0);
    WayfinderFoundationConnectionState *state = [WayfinderFoundationConnectionState new];
    state.completed = completed;
    id<WayfinderFoundationModelBrokerProtocol> proxy =
        proxy_for_connection(connection, state);
    WayfinderFoundationStreamReceiver *receiver = [WayfinderFoundationStreamReceiver new];
    receiver.callback = callback;
    receiver.context = context;
    receiver.completed = completed;
    [proxy stream:requestData to:receiver];

    int64_t nanos = (int64_t)(timeoutSeconds * (double)NSEC_PER_SEC);
    long waitStatus = dispatch_semaphore_wait(completed, dispatch_time(DISPATCH_TIME_NOW, nanos));
    [connection invalidate];
    if (waitStatus != 0) return WF_FOUNDATION_TIMED_OUT;
    if (state.failed) return WF_FOUNDATION_UNAVAILABLE;
    return status_for_error(receiver.errorCode);
}

int wayfinder_foundation_xpc_cancel(const char *requestIDBytes, double timeoutSeconds) {
    if (requestIDBytes == NULL || !(timeoutSeconds > 0.0)) return WF_FOUNDATION_REQUEST_TOO_LARGE;
    NSString *requestID = [NSString stringWithUTF8String:requestIDBytes];
    if (requestID == nil || requestID.length == 0) return WF_FOUNDATION_REQUEST_TOO_LARGE;
    NSXPCConnection *connection = [[NSXPCConnection alloc]
        initWithServiceName:@"com.wayfinder.FoundationModelBroker"];
    dispatch_semaphore_t completed = dispatch_semaphore_create(0);
    WayfinderFoundationConnectionState *state = [WayfinderFoundationConnectionState new];
    state.completed = completed;
    __block NSString *replyError = nil;
    id<WayfinderFoundationModelBrokerProtocol> proxy =
        proxy_for_connection(connection, state);
    [proxy cancel:requestID withReply:^(NSString *errorCode) {
        replyError = errorCode;
        dispatch_semaphore_signal(completed);
    }];
    int64_t nanos = (int64_t)(timeoutSeconds * (double)NSEC_PER_SEC);
    long waitStatus = dispatch_semaphore_wait(completed, dispatch_time(DISPATCH_TIME_NOW, nanos));
    [connection invalidate];
    if (waitStatus != 0) return WF_FOUNDATION_TIMED_OUT;
    if (state.failed) return WF_FOUNDATION_UNAVAILABLE;
    return status_for_error(replyError);
}
