import XCTest

final class PhoneBridgeFixtureUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testOpenContactTypeAndSendInsideFixture() throws {
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.navigationBars["PhoneBridge"].waitForExistence(timeout: 12))
        let contact = app.buttons["contact.xiaoming-01"]
        XCTAssertTrue(contact.waitForExistence(timeout: 8))
        contact.tap()
        let input = app.textFields["chat.input"]
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        input.tap()
        input.typeText("Hello PhoneBridge")
        XCTAssertEqual(input.value as? String, "Hello PhoneBridge")
        app.buttons["chat.send"].tap()
        XCTAssertTrue(app.staticTexts["chat.message.0"].waitForExistence(timeout: 8))
        XCTAssertEqual(app.staticTexts["chat.message.0"].label, "Hello PhoneBridge")
    }

    func testDistinctContactsExist() throws {
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.buttons["contact.alex-01"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.buttons["contact.alex-02"].exists)
    }
}
