import XCTest

final class PhoneBridgeFixtureUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    private func openWhatsAppFixture(_ app: XCUIApplication) {
        let entry = app.buttons["app.whatsapp"]
        XCTAssertTrue(entry.waitForExistence(timeout: 8))
        entry.tap()
    }

    func testOpenContactTypeAndSendInsideWhatsAppFixture() throws {
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.navigationBars["PhoneBridge"].waitForExistence(timeout: 12))
        openWhatsAppFixture(app)
        let contact = app.buttons["contact.contact-02"]
        XCTAssertTrue(contact.waitForExistence(timeout: 8))
        contact.tap()
        XCTAssertTrue(app.navigationBars["Casey"].waitForExistence(timeout: 8))
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
        XCTAssertTrue(app.navigationBars["PhoneBridge"].waitForExistence(timeout: 12))
        openWhatsAppFixture(app)
        XCTAssertTrue(app.buttons["contact.alex-01"].waitForExistence(timeout: 8))
        XCTAssertTrue(app.buttons["contact.alex-02"].exists)
    }

    func testComposeAndReadBackInsideXFixture() throws {
        let app = XCUIApplication()
        app.launch()
        XCTAssertTrue(app.navigationBars["PhoneBridge"].waitForExistence(timeout: 12))
        let entry = app.buttons["app.x"]
        XCTAssertTrue(entry.waitForExistence(timeout: 8))
        entry.tap()
        XCTAssertTrue(app.navigationBars["X（合成）"].waitForExistence(timeout: 8))
        let input = app.textFields["compose.input"]
        XCTAssertTrue(input.waitForExistence(timeout: 8))
        input.tap()
        input.typeText("Hello PhoneBridge")
        XCTAssertEqual(input.value as? String, "Hello PhoneBridge")
        app.buttons["compose.post"].tap()
        let post = app.staticTexts["timeline.post.0"]
        XCTAssertTrue(post.waitForExistence(timeout: 8))
        XCTAssertEqual(post.label, "Hello PhoneBridge")
    }
}
