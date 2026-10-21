#import <AppKit/AppKit.h>

int main(void) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
        NSTask *task = [[NSTask alloc] init];
        task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/python3"];
        NSString *script = [[[NSBundle mainBundle] resourcePath]
            stringByAppendingPathComponent:@"launch.py"];
        task.arguments = @[script];
        task.standardOutput = [NSFileHandle fileHandleWithNullDevice];
        NSPipe *errors = [NSPipe pipe];
        task.standardError = errors;
        NSError *error = nil;
        BOOL launched = [task launchAndReturnError:&error];
        NSData *output = launched ? [[errors fileHandleForReading] readDataToEndOfFile] : nil;
        if (launched) [task waitUntilExit];
        if (!launched || task.terminationStatus != 0) {
            NSString *detail = launched ? [[NSString alloc] initWithData:output encoding:NSUTF8StringEncoding] : error.localizedDescription;
            NSAlert *alert = [[NSAlert alloc] init];
            alert.messageText = @"Resonance could not open";
            alert.informativeText = detail.length ? detail : @"Try again, or ask your assistant to repair the Mac launcher. Your saved data is kept.";
            [alert addButtonWithTitle:@"OK"];
            [NSApp activateIgnoringOtherApps:YES];
            [alert runModal];
            return 1;
        }
    }
    return 0;
}
